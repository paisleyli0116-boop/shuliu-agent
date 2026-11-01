from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
from collections.abc import Callable, Mapping
from copy import deepcopy
from uuid import uuid4

from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.policy.rules import PolicyDecision
from shuliu_runtime.tools.broker import IdentityPrincipal, ToolBroker, ToolExecutionError
from shuliu_runtime.tools.idempotency import IdempotencyStore
from shuliu_runtime.tools.registry import ToolDefinition, ToolRegistry

from .ufo_operator import UfoOperator
from .verification import ComputerError, snapshot_fingerprint, verify_action


_WINDOW_REF = {"type": "string", "minLength": 1, "maxLength": 32}
_OBSERVATION_ID = {"type": "string", "minLength": 1, "maxLength": 64}
_ELEMENT_REF = {"type": "string", "minLength": 1, "maxLength": 64}
_ACT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["click", "type", "scroll"]},
        "window_ref": _WINDOW_REF,
        "observation_id": _OBSERVATION_ID,
        "element_ref": _ELEMENT_REF,
        "text_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "delta_y": {"type": "integer", "minimum": -1200, "maximum": 1200, "not": {"const": 0}},
        "expected_text": {"type": "string", "minLength": 1, "maxLength": 256},
    },
    "required": ["kind", "window_ref", "observation_id", "element_ref"],
    "additionalProperties": False,
    "oneOf": [
        {
            "properties": {"kind": {"const": "click"}},
            "not": {"anyOf": [{"required": ["text_digest"]}, {"required": ["delta_y"]}]},
        },
        {
            "properties": {"kind": {"const": "type"}},
            "required": ["text_digest"],
            "not": {"anyOf": [{"required": ["delta_y"]}, {"required": ["expected_text"]}]},
        },
        {
            "properties": {"kind": {"const": "scroll"}},
            "required": ["delta_y"],
            "not": {"anyOf": [{"required": ["text_digest"]}, {"required": ["expected_text"]}]},
        },
    ],
}
_OBJECT_SCHEMA = {
    "type": "object",
    "properties": {
        "evidence_ref": {
            "type": "string",
            "minLength": 1,
            "maxLength": 256,
            "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}(?![\s\S])",
        }
    },
    "required": ["evidence_ref"],
}


class _WindowsBackend:
    def __init__(self) -> None:
        from .uia_backend import UiaBackend
        from .win32_backend import Win32Backend

        self._win32 = Win32Backend()
        self._uia = UiaBackend()

    def capability(self) -> dict[str, object]:
        return self._win32.capability()

    def observe(self, window_ref: str) -> dict[str, object]:
        info = self._win32.inspect_window(window_ref)
        return self._uia.observe(window_ref, info)

    def perform(self, window_ref: str, action: Mapping[str, object], element_id: str) -> None:
        self._uia.perform(window_ref, action, element_id)


class ComputerRuntime:
    """Policy-controlled Windows UI automation entry point."""

    def __init__(
        self,
        *,
        identity,
        policy,
        approvals,
        idempotency_store: IdempotencyStore | None,
        event_sink: Callable,
        evidence_sink: Callable[[dict[str, object]], str],
        backend=None,
        operator: UfoOperator | None = None,
    ) -> None:
        if not callable(evidence_sink):
            raise TypeError("evidence_sink must be callable")
        self.registry = ToolRegistry()
        self._executor = _ComputerExecutor(
            backend or _WindowsBackend(), evidence_sink=evidence_sink
        )
        self.operator = operator or UfoOperator()
        self._text_key = secrets.token_bytes(32)
        self._dispatch_lock = threading.RLock()
        definitions = (
            ToolDefinition(
                "computer.capability", "1.0.0", {"type": "object", "properties": {}, "additionalProperties": False},
                _OBJECT_SCHEMA, ("computer.read",), RiskLevel.LOW, False,
            ),
            ToolDefinition(
                "computer.observe", "1.0.0",
                {"type": "object", "properties": {"window_ref": _WINDOW_REF}, "required": ["window_ref"], "additionalProperties": False},
                _OBJECT_SCHEMA, ("computer.read",), RiskLevel.LOW, False,
            ),
            ToolDefinition(
                "computer.act", "1.0.0", _ACT_SCHEMA,
                _OBJECT_SCHEMA, ("computer.act",), RiskLevel.CRITICAL, True,
            ),
        )
        for definition in definitions:
            self.registry.register(definition, self._executor)
        self.broker = ToolBroker(
            registry=self.registry,
            identity=identity,
            policy=policy,
            approvals=approvals,
            event_sink=event_sink,
            idempotency_store=idempotency_store,
        )

    def capability(self, *, actor, context):
        return self.broker.execute(
            "computer.capability", {}, actor, context, idempotency_key=None
        )

    def observe(self, window_ref: str, *, actor, context):
        safe_context = _target_context(context, window_ref)
        return self.broker.execute(
            "computer.observe",
            {"window_ref": window_ref},
            actor,
            safe_context,
            idempotency_key=None,
        )

    def prepare_approval_context(
        self, action, *, actor, context
    ) -> tuple[dict[str, object], PolicyDecision]:
        """Prepare the exact safe action digest and Policy result for approval."""
        broker_action, _pending_text, _digest = self._broker_action(action)
        window_ref = broker_action.get("window_ref")
        if not isinstance(window_ref, str):
            raise ToolExecutionError(
                "TOOL_INPUT_INVALID", "Computer window reference is invalid"
            )
        safe_context = _target_context(context, window_ref)
        return self.broker.prepare_approval_context(
            "computer.act", broker_action, actor, safe_context
        )

    def act(self, action, *, actor, context, idempotency_key: str | None):
        broker_action, pending_text, digest = self._broker_action(action)
        window_ref = broker_action.get("window_ref")
        if not isinstance(window_ref, str):
            raise ToolExecutionError("TOOL_INPUT_INVALID", "Computer window reference is invalid")
        safe_context = _target_context(context, window_ref)
        with self._dispatch_lock:
            if digest is not None and pending_text is not None:
                self._executor.provide_text(digest, pending_text)
            try:
                return self.broker.execute(
                    "computer.act",
                    broker_action,
                    actor,
                    safe_context,
                    idempotency_key=idempotency_key,
                )
            finally:
                if digest is not None:
                    self._executor.clear_text(digest)

    def _broker_action(
        self, action
    ) -> tuple[dict[str, object], str | None, str | None]:
        if not isinstance(action, Mapping):
            raise ToolExecutionError("TOOL_INPUT_INVALID", "Computer action must be an object")
        broker_action = dict(action)
        pending_text: str | None = None
        digest: str | None = None
        if broker_action.get("kind") == "type":
            pending_text = broker_action.pop("text", None)
            if not isinstance(pending_text, str) or len(pending_text) > 4096:
                raise ToolExecutionError("TOOL_INPUT_INVALID", "Computer text input is invalid")
            digest = hmac.new(
                self._text_key, pending_text.encode("utf-8"), hashlib.sha256
            ).hexdigest()
            broker_action["text_digest"] = digest
        return broker_action, pending_text, digest

    def act_operator_prediction(
        self,
        prediction: Mapping[str, object],
        observation: Mapping[str, object],
        *,
        actor,
        context,
        idempotency_key: str | None,
    ):
        action = self.operator.to_action(prediction, observation)
        return self.act(
            action,
            actor=actor,
            context=context,
            idempotency_key=idempotency_key,
        )


class _ComputerExecutor:
    def __init__(self, backend, *, evidence_sink: Callable[[dict[str, object]], str]) -> None:
        self.backend = backend
        self.evidence = evidence_sink
        self._lock = threading.RLock()
        self._latest: dict[str, dict[str, object]] = {}
        self._pending_text: dict[str, str] = {}
        self._last_capability: dict[str, object] = {}

    def provide_text(self, digest: str, text: str) -> None:
        with self._lock:
            self._pending_text[digest] = text

    def clear_text(self, digest: str) -> None:
        with self._lock:
            self._pending_text.pop(digest, None)

    def execute(self, tool_name, args, context, idempotency_key):
        with self._lock:
            operation = tool_name.removeprefix("computer.")
            if operation == "capability":
                capability = self._capability()
                output = dict(capability)
                output["evidence_ref"] = self._evidence(
                    context, "capability", "RESULT", status=capability["status"]
                )
                return output

            capability = self._capability()
            if capability.get("status") != "AVAILABLE":
                code = _safe_error_code(capability.get("reason_code"), "COMPUTER_SESSION_UNAVAILABLE")
                output = self._result("UNAVAILABLE", context, args, error_code=code)
                output["evidence_ref"] = self._evidence(
                    context, operation, "RESULT", status="UNAVAILABLE", error_code=code
                )
                return output

            window_ref = str(args["window_ref"])
            if operation == "observe":
                try:
                    snapshot = _validated_snapshot(self.backend.observe(window_ref), window_ref)
                except ComputerError as error:
                    output = self._result("FAILED", context, args, error_code=error.code)
                    output["evidence_ref"] = self._evidence(
                        context, operation, "RESULT", status="FAILED", error_code=error.code
                    )
                    return output
                observation, element_refs = self._public_observation(snapshot)
                self._latest[window_ref] = {
                    "observation": observation,
                    "fingerprint": snapshot_fingerprint(snapshot),
                    "element_refs": element_refs,
                }
                evidence_ref = self._evidence(
                    context,
                    "observe",
                    "RESULT",
                    status="OBSERVED",
                    observation=_evidence_observation(observation),
                )
                return {**observation, "status": "OBSERVED", "evidence_ref": evidence_ref}
            return self._act(args, context)

    def verify(self, tool_name, args, output, context) -> bool:
        if not isinstance(output, dict) or not isinstance(output.get("evidence_ref"), str):
            return False
        operation = tool_name.removeprefix("computer.")
        if operation == "capability":
            return output.get("status") in ("AVAILABLE", "UNAVAILABLE")
        if operation == "observe":
            return output.get("status") in ("OBSERVED", "UNAVAILABLE", "FAILED")
        return output.get("status") == "VERIFIED" and output.get("retryable") is False

    def _capability(self) -> dict[str, object]:
        try:
            value = self.backend.capability()
        except Exception:
            value = {"status": "UNAVAILABLE", "reason_code": "COMPUTER_SESSION_STATE_UNKNOWN"}
        if not isinstance(value, Mapping) or value.get("status") != "AVAILABLE":
            reason = _safe_error_code(
                value.get("reason_code") if isinstance(value, Mapping) else None,
                "COMPUTER_SESSION_STATE_UNKNOWN",
            )
            self._last_capability = {
                "status": "UNAVAILABLE",
                "os": "windows",
                "reason_code": reason,
                "session_id": value.get("session_id") if isinstance(value, Mapping) else None,
                "desktop_name": value.get("desktop_name") if isinstance(value, Mapping) else None,
                "machine": value.get("machine") if isinstance(value, Mapping) else None,
            }
            return dict(self._last_capability)
        self._last_capability = {
            "status": "AVAILABLE",
            "os": "windows",
            "reason_code": None,
            "session_id": value.get("session_id"),
            "desktop_name": value.get("desktop_name"),
            "machine": value.get("machine"),
        }
        return dict(self._last_capability)

    def _act(self, args: Mapping[str, object], context: Mapping[str, object]) -> dict[str, object]:
        window_ref = str(args["window_ref"])
        kind = str(args["kind"])
        prior = self._latest.get(window_ref)
        if prior is None or prior["observation"].get("observation_id") != args.get("observation_id"):
            return self._failed(context, args, "COMPUTER_OBSERVATION_STALE")
        element_id = prior["element_refs"].get(args.get("element_ref"))
        if not isinstance(element_id, str):
            return self._failed(context, args, "COMPUTER_ELEMENT_STALE")
        try:
            before = _validated_snapshot(self.backend.observe(window_ref), window_ref)
        except ComputerError as error:
            return self._failed(context, args, error.code)
        if snapshot_fingerprint(before) != prior["fingerprint"]:
            return self._failed(context, args, "COMPUTER_OBSERVATION_STALE")
        target = _find_element(before, element_id)
        if target is None or target.get("enabled") is not True:
            return self._failed(context, args, "COMPUTER_ELEMENT_STALE")
        if kind == "type" and target.get("sensitive") is True:
            return self._failed(context, args, "COMPUTER_SENSITIVE_TARGET_DENIED")
        if kind == "scroll" and target.get("scroll_percent") is None:
            return self._failed(context, args, "COMPUTER_SCROLL_UNAVAILABLE")
        action: dict[str, object] = {key: value for key, value in args.items()}
        if kind == "type":
            digest = args.get("text_digest")
            text = self._pending_text.get(digest) if isinstance(digest, str) else None
            if not isinstance(text, str):
                return self._failed(context, args, "COMPUTER_TEXT_UNAVAILABLE")
            action.pop("text_digest", None)
            action["text"] = text
        before_safe = _safe_snapshot(before)
        try:
            before_ref = self._evidence(
                context, "act", "BEFORE", status="READY", observation=before_safe,
                action_kind=kind, target_role=target.get("control_type"),
            )
        except ComputerError as error:
            return self._failed(context, args, error.code)
        dispatched = False
        try:
            dispatched = True
            self.backend.perform(window_ref, action, element_id)
            after = _validated_snapshot(self.backend.observe(window_ref), window_ref)
            is_verified = verify_action(action, before, after, element_id)
            status = "VERIFIED" if is_verified else "UNVERIFIED"
            error_code = None if is_verified else "COMPUTER_RESULT_UNVERIFIED"
            after_ref = self._evidence(
                context, "act", "AFTER", status=status, observation=_safe_snapshot(after),
                action_kind=kind, target_role=target.get("control_type"), error_code=error_code,
            )
            return {
                "status": status,
                "trace_id": context["trace_id"],
                "window_ref": window_ref,
                "action_kind": kind,
                "retryable": False,
                "outcome_unknown": not is_verified,
                "error_code": error_code,
                "before_evidence_ref": before_ref,
                "evidence_ref": after_ref,
            }
        except Exception as error:
            code = error.code if isinstance(error, ComputerError) else "COMPUTER_CONNECTION_LOST"
            if dispatched:
                try:
                    after = _validated_snapshot(self.backend.observe(window_ref), window_ref)
                    recovered = verify_action(action, before, after, element_id)
                    status = "VERIFIED" if recovered else "UNVERIFIED"
                    result_ref = self._evidence(
                        context, "act", "AFTER_RECOVERY", status=status,
                        observation=_safe_snapshot(after), action_kind=kind,
                        dispatch_error_code=code,
                        error_code=None if recovered else "COMPUTER_RESULT_UNVERIFIED",
                    )
                    return {
                        "status": status,
                        "trace_id": context["trace_id"],
                        "window_ref": window_ref,
                        "action_kind": kind,
                        "retryable": False,
                        "outcome_unknown": not recovered,
                        "error_code": None if recovered else "COMPUTER_RESULT_UNVERIFIED",
                        "before_evidence_ref": before_ref,
                        "evidence_ref": result_ref,
                    }
                except Exception as recovery_error:
                    recovery_code = (
                        recovery_error.code
                        if isinstance(recovery_error, ComputerError)
                        else "COMPUTER_RECONCILIATION_FAILED"
                    )
                    try:
                        result_ref = self._evidence(
                            context, "act", "RESULT", status="UNVERIFIED",
                            action_kind=kind, dispatch_error_code=code,
                            error_code=recovery_code, outcome_unknown=True,
                        )
                    except Exception:
                        result_ref = None
                    # ToolBroker records TOOL_EXECUTION_UNKNOWN; the action is never retried.
                    raise RuntimeError(
                        f"computer action outcome unknown:{result_ref or 'unrecorded'}"
                    ) from None
            return self._failed(context, args, code)

    def _failed(
        self, context: Mapping[str, object], args: Mapping[str, object], code: str
    ) -> dict[str, object]:
        output = self._result("FAILED", context, args, error_code=code)
        output["evidence_ref"] = self._evidence(
            context, "act", "RESULT", status="FAILED", error_code=code,
            action_kind=args.get("kind"),
        )
        return output

    @staticmethod
    def _result(
        status: str,
        context: Mapping[str, object],
        args: Mapping[str, object],
        *,
        error_code: str | None = None,
    ) -> dict[str, object]:
        return {
            "status": status,
            "trace_id": context["trace_id"],
            "window_ref": args.get("window_ref"),
            "action_kind": args.get("kind"),
            "retryable": False,
            "outcome_unknown": False,
            "error_code": error_code,
        }

    def _public_observation(
        self, snapshot: Mapping[str, object]
    ) -> tuple[dict[str, object], dict[str, str]]:
        element_refs: dict[str, str] = {}
        elements: list[dict[str, object]] = []
        raw_elements = snapshot["elements"]
        assert isinstance(raw_elements, list)
        for item in raw_elements:
            assert isinstance(item, Mapping)
            reference = str(uuid4())
            element_refs[reference] = str(item["element_id"])
            elements.append(
                {
                    "element_ref": reference,
                    "role": item["control_type"],
                    "name": item["name"],
                    "bounds": list(item["bounds"]),
                    "sensitive": item["sensitive"],
                    "enabled": item["enabled"],
                    "focused": item["focused"],
                    "scrollable": item["scroll_percent"] is not None,
                }
            )
        observation = {
            "window_ref": snapshot["window_ref"],
            "observation_id": str(uuid4()),
            "title": snapshot["title"],
            "process_id": snapshot["process_id"],
            "bounds": list(snapshot["bounds"]),
            "dpi": snapshot["dpi"],
            "elements": elements,
            "trust": "UNTRUSTED_UI_CONTENT",
        }
        return observation, element_refs

    def _evidence(self, context, operation, phase, **extra) -> str:
        capability = self._last_capability
        record = {
            "schema_version": 1,
            "trace_id": context["trace_id"],
            "organization_id": context["organization_id"],
            "machine": capability.get("machine"),
            "session": {
                "state": capability.get("status"),
                "session_id": capability.get("session_id"),
                "desktop_name": capability.get("desktop_name"),
            },
            "operation": operation,
            "phase": phase,
            **extra,
        }
        reference = self.evidence(record)
        if not isinstance(reference, str) or not reference.strip():
            raise ComputerError("COMPUTER_EVIDENCE_UNAVAILABLE")
        return reference


def _target_context(context: Mapping[str, object], window_ref: str) -> dict[str, object]:
    if not isinstance(context, Mapping):
        raise ToolExecutionError("TOOL_CONTEXT_INVALID", "Computer context is invalid")
    safe = dict(context)
    raw_resource = safe.get("resource")
    if raw_resource is not None and not isinstance(raw_resource, Mapping):
        raise ToolExecutionError("TOOL_CONTEXT_INVALID", "Computer resource is invalid")
    resource = dict(raw_resource) if isinstance(raw_resource, Mapping) else {}
    resource_id = resource.get("resource_id")
    if resource_id is not None and resource_id != window_ref:
        raise ToolExecutionError("COMPUTER_RESOURCE_SCOPE_MISMATCH", "Computer window is outside the requested resource")
    resource.setdefault("organization_id", safe.get("organization_id"))
    resource.setdefault("resource_type", "computer_window")
    resource["resource_id"] = window_ref
    safe["resource"] = resource
    return safe


def _validated_snapshot(value: object, window_ref: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or value.get("window_ref") != window_ref:
        raise ComputerError("COMPUTER_OBSERVATION_INVALID")
    title = value.get("title")
    process_id = value.get("process_id")
    bounds = value.get("bounds")
    dpi = value.get("dpi")
    raw_elements = value.get("elements")
    if (
        not isinstance(title, str)
        or isinstance(process_id, bool)
        or not isinstance(process_id, int)
        or process_id <= 0
        or not _rect(bounds)
        or isinstance(dpi, bool)
        or not isinstance(dpi, int)
        or dpi <= 0
        or not isinstance(raw_elements, list)
        or len(raw_elements) > 300
    ):
        raise ComputerError("COMPUTER_OBSERVATION_INVALID")
    elements: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in raw_elements:
        if not isinstance(raw, Mapping):
            raise ComputerError("COMPUTER_OBSERVATION_INVALID")
        element_id = raw.get("element_id")
        runtime_id = raw.get("runtime_id")
        role = raw.get("control_type")
        name = raw.get("name", "")
        sensitive = raw.get("sensitive")
        enabled = raw.get("enabled")
        focused = raw.get("focused", False)
        scroll_percent = raw.get("scroll_percent")
        element_bounds = raw.get("bounds")
        if (
            not isinstance(element_id, str)
            or not element_id
            or element_id in seen
            or not isinstance(runtime_id, (list, tuple))
            or not runtime_id
            or len(runtime_id) > 64
            or any(
                isinstance(item, bool) or not isinstance(item, int)
                for item in runtime_id
            )
            or not isinstance(role, str)
            or not role
            or not isinstance(name, str)
            or not isinstance(sensitive, bool)
            or not isinstance(enabled, bool)
            or not isinstance(focused, bool)
            or (
                scroll_percent is not None
                and (
                    isinstance(scroll_percent, bool)
                    or not isinstance(scroll_percent, int)
                    or not 0 <= scroll_percent <= 100
                )
            )
            or not _rect(element_bounds)
        ):
            raise ComputerError("COMPUTER_OBSERVATION_INVALID")
        seen.add(element_id)
        detected_sensitive = sensitive or bool(
            re.search(r"(?i)(password|passcode|secret|token|api\s*key|credential)", name)
        )
        item: dict[str, object] = {
            "element_id": element_id,
            "runtime_id": list(runtime_id),
            "control_type": _safe_text(role, 64),
            "name": "[REDACTED]" if detected_sensitive else _safe_text(name, 256),
            "sensitive": detected_sensitive,
            "enabled": enabled,
            "focused": focused,
            "bounds": list(element_bounds),
            "scroll_percent": scroll_percent,
            "value": None if detected_sensitive else _safe_scalar(raw.get("value")),
        }
        elements.append(item)
    return {
        "window_ref": window_ref,
        "title": _safe_text(title, 512),
        "process_id": process_id,
        "bounds": list(bounds),
        "dpi": dpi,
        "elements": elements,
        "scroll_y": value.get("scroll_y") if isinstance(value.get("scroll_y"), int) else None,
    }


def _safe_snapshot(snapshot: Mapping[str, object]) -> dict[str, object]:
    elements = snapshot.get("elements")
    safe_elements = []
    if isinstance(elements, list):
        for item in elements:
            if isinstance(item, Mapping):
                safe_elements.append(
                    {
                        "element_id": item.get("element_id"),
                        "control_type": item.get("control_type"),
                        "name": "[OMITTED]",
                        "sensitive": item.get("sensitive"),
                        "enabled": item.get("enabled"),
                        "focused": item.get("focused"),
                        "bounds": item.get("bounds"),
                        "scroll_percent": item.get("scroll_percent"),
                        "value": None,
                    }
                )
    return {
        "window_ref": snapshot.get("window_ref"),
        "title": "[OMITTED]",
        "process_id": snapshot.get("process_id"),
        "bounds": snapshot.get("bounds"),
        "dpi": snapshot.get("dpi"),
        "elements": safe_elements,
        "scroll_y": snapshot.get("scroll_y"),
        "trust": "UNTRUSTED_UI_CONTENT",
    }


def _evidence_observation(
    observation: Mapping[str, object],
) -> dict[str, object]:
    safe = dict(observation)
    safe["title"] = "[OMITTED]"
    elements = observation.get("elements")
    if isinstance(elements, list):
        safe["elements"] = [
            {**item, "name": "[OMITTED]"}
            for item in elements
            if isinstance(item, Mapping)
        ]
    return safe


def _find_element(snapshot: Mapping[str, object], element_id: str) -> Mapping[str, object] | None:
    elements = snapshot.get("elements")
    if isinstance(elements, list):
        return next(
            (item for item in elements if isinstance(item, Mapping) and item.get("element_id") == element_id),
            None,
        )
    return None


def _rect(value: object) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 4
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        and value[2] > value[0]
        and value[3] > value[1]
    )


def _safe_scalar(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return _safe_text(value, 8192) if isinstance(value, str) else value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    return None


def _safe_text(value: object, limit: int) -> str:
    text = str(value)[:limit]
    text = re.sub(
        r"(?i)\b(password|secret|api[_ -]?key|token|authorization|cookie)\s*[:=]\s*[^\s;,]+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", text)
    return text[:limit]


def _safe_error_code(value: object, default: str) -> str:
    if isinstance(value, str) and re.fullmatch(r"COMPUTER_[A-Z0-9_]+", value):
        return value
    return default
