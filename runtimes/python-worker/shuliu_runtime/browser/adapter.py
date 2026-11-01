from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from threading import Event, RLock
from uuid import uuid4

from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.tools.broker import IdentityPrincipal, ToolBroker
from shuliu_runtime.tools.idempotency import SAFE_TRACE_OR_SESSION_ID_SCHEMA_PATTERN
from shuliu_runtime.tools.registry import ToolDefinition, ToolRegistry

from .session import SessionStore
from .verification import BrowserError, check_url, origin, safe_text, safe_url, verified


_READ = {"launch", "observe", "close"}
_TEXT = {"type": "string", "minLength": 1, "maxLength": 8192}
_SESSION_ID = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": SAFE_TRACE_OR_SESSION_ID_SCHEMA_PATTERN,
}


def _input(operation):
    properties = {} if operation == "launch" else {"session_id": _SESSION_ID}
    if operation == "navigate":
        properties["url"] = _TEXT
    if operation in ("click", "type", "scroll"):
        properties["observation_id"] = _TEXT
    if operation in ("click", "type"):
        properties.update(element_ref=_TEXT, expected_text=_TEXT)
    if operation == "type":
        properties["text"] = {"type": "string", "maxLength": 4096}
    if operation == "scroll":
        properties["delta_y"] = {"type": "integer", "minimum": -2000, "maximum": 2000, "not": {"const": 0}}
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


class BrowserRuntime:
    """Public browser entry point: every requested operation goes through Broker.

    No production execution gate is supplied until Task 33's durable capability
    is verified. Tests inject a loopback-only fixture gate, never real accounts.
    Runtime.close() is host lifecycle cleanup, not a caller's Tool operation.
    """
    def __init__(self, *, identity, policy, approvals, idempotency_store, event_sink,
                 evidence_sink, profile_root: Path, allowed_origins: tuple[str, ...],
                 backend_factory=None, execution_gate=None, session_ttl=900, timeout=15):
        if backend_factory is None:
            from .backend import BrowserUseBackend
            backend_factory = BrowserUseBackend
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        output_schema = json.loads((Path(__file__).resolve().parents[4] /
            "packages/protocols/schemas/browser-tool-v1.schema.json").read_text(encoding="utf-8"))
        self.registry = ToolRegistry()
        self._executor = _BrowserExecutor(profile_root, backend_factory, session_ttl,
            allowed_origins, execution_gate, evidence_sink, timeout)
        for operation in ("launch", "navigate", "observe", "click", "type", "scroll", "close"):
            self.registry.register(ToolDefinition(
                name="browser." + operation, version="1.0.0", input_schema=_input(operation),
                output_schema=output_schema,
                permissions=("browser.read" if operation in _READ else "browser.act",),
                risk_level=RiskLevel.LOW if operation in _READ else RiskLevel.CRITICAL,
                idempotency_required=operation not in _READ), self._executor)
        self.broker = ToolBroker(registry=self.registry, identity=identity, policy=policy,
            approvals=approvals, event_sink=event_sink, idempotency_store=idempotency_store)

    def call(self, operation, args, *, actor, context, idempotency_key=None):
        return self.broker.execute("browser." + operation, args, actor, context, idempotency_key=idempotency_key)

    def observe(self, session_id, *, actor, context):
        return self.call("observe", {"session_id": session_id}, actor=actor, context=context)

    def act(self, action, policy_context, *, actor, idempotency_key):
        operation = action["operation"]
        if operation not in ("navigate", "click", "type", "scroll"):
            raise BrowserError("BROWSER_ACTION_INVALID")
        return self.call(operation, {key: value for key, value in action.items() if key != "operation"},
            actor=actor, context=policy_context, idempotency_key=idempotency_key)

    def close(self):
        with self._executor.lock:
            self._executor.sessions.close_all()


class _BrowserExecutor:
    def __init__(self, root, factory, ttl, allowed_origins, gate, evidence, timeout):
        self.sessions = SessionStore(root, factory, ttl)
        self.allowed = frozenset(origin(url) for url in allowed_origins)
        self.gate, self.evidence, self.timeout, self.lock = gate, evidence, timeout, RLock()

    def execute(self, tool_name, args, context, idempotency_key):
        with self.lock:
            return self._execute(tool_name.removeprefix("browser."), dict(args), context)

    def _execute(self, operation, args, context):
        session, dispatched = None, False
        result = {"status": "FAILED", "trace_id": context["trace_id"], "retryable": False,
                  "outcome_unknown": False, "session_id": args.get("session_id")}
        try:
            principal = context.get("_principal")
            if not isinstance(principal, IdentityPrincipal):
                raise BrowserError("BROWSER_BROKER_REQUIRED")
            cancel = context.get("cancel_event")
            if cancel is not None and not isinstance(cancel, Event):
                raise BrowserError("BROWSER_CONTEXT_INVALID")
            if operation == "launch":
                session = self.sessions.create(principal)
                result["session_id"] = session.session_id
                self._check_cancel(cancel)
                metadata = self._request(session, "start", {"profile": session.directory.name,
                    "allowed_origins": sorted(self.allowed)}, cancel)
                result["browser"] = {"browser_version": safe_text(metadata.get("browser_version", ""), 100),
                                     "process_id": metadata.get("process_id")}
            else:
                session = self.sessions.get(args["session_id"], principal, closing=operation == "close")
                if operation == "close":
                    self.sessions.close(session)
                else:
                    self._check_cancel(cancel)
                    if operation == "observe":
                        raw = self._request(session, "observe", {}, cancel)
                        result["observation"] = self._observation(session, raw)
                    else:
                        if operation == "navigate":
                            check_url(args["url"], self.allowed)
                            if args["url"] == "about:blank":
                                raise BrowserError("BROWSER_TARGET_DENIED")
                            target = args["url"]
                        else:
                            if session.observation is None or args["observation_id"] != session.observation["observation_id"]:
                                raise BrowserError("BROWSER_OBSERVATION_STALE")
                            target = session.raw["url"]
                        if self.gate is None or self.gate.authorize(operation, target) is not True:
                            raise BrowserError("BROWSER_EXECUTION_GATE_CLOSED")
                        before = self._request(session, "observe", {}, cancel)
                        check_url(before["url"], self.allowed)
                        payload = dict(args)
                        if operation != "navigate":
                            payload["expected_page"] = {"url": before["url"], "account": before["account"]}
                            if before["account"] != session.raw["account"]:
                                raise BrowserError("BROWSER_ACCOUNT_CHANGED")
                            if before["url"] != session.raw["url"]:
                                raise BrowserError("BROWSER_TARGET_CHANGED")
                            if operation in ("click", "type"):
                                element = session.elements.get(args["element_ref"])
                                if not element or element not in before["elements"]:
                                    raise BrowserError("BROWSER_ELEMENT_STALE")
                                payload["node_id"] = element["node_id"]
                                payload["expected_element"] = deepcopy(element)
                        # Persist redacted pre-action evidence before dispatch.
                        before_ref = self._evidence(context, session, operation, "BEFORE", self._safe_page(before))
                        session.observation = None
                        self._check_cancel(cancel)
                        dispatched = True
                        self._request(session, operation, payload, cancel)
                        after = self._request(session, "observe", {}, cancel)
                        check_url(after["url"], self.allowed)
                        result["status"] = "VERIFIED" if verified(operation, args, before, after) else "UNVERIFIED"
                        result["before_evidence_ref"] = before_ref
                        result["observation"] = self._observation(session, after)
                        if result["status"] != "VERIFIED":
                            result["error_code"] = "BROWSER_RESULT_UNVERIFIED"
                            result["outcome_unknown"] = True
                            session.quarantined = True
            if result["status"] == "FAILED":
                result["status"] = "VERIFIED"
        except Exception as error:
            code = error.code if isinstance(error, BrowserError) else (
                "BROWSER_CANCELLED" if isinstance(error, InterruptedError) else
                "BROWSER_TIMEOUT" if isinstance(error, TimeoutError) else "BROWSER_CONNECTION_LOST")
            result.update(status="UNVERIFIED" if dispatched else "FAILED", error_code=code, outcome_unknown=dispatched)
            if session is not None:
                if dispatched:
                    session.quarantined = True
                if operation == "launch" or isinstance(error, (InterruptedError, TimeoutError, ConnectionError)) or code in (
                    "BROWSER_CANCELLED", "BROWSER_CONNECTION_LOST", "BROWSER_PROTOCOL_INVALID", "BROWSER_TIMEOUT"):
                    self.sessions.close(session)
        result["evidence_ref"] = self._evidence(context, session, operation, "RESULT", result.get("observation"),
            status=result["status"], error_code=result.get("error_code"))
        return result

    def _request(self, session, operation, payload, cancel):
        return session.backend.request(operation, payload, timeout=self.timeout, cancel_event=cancel)

    @staticmethod
    def _check_cancel(cancel):
        if cancel is not None and cancel.is_set():
            raise BrowserError("BROWSER_CANCELLED")

    @staticmethod
    def _safe_page(raw):
        return {"url": safe_url(raw["url"]), "text": safe_text(raw.get("text", "")),
                "trust": "UNTRUSTED_PAGE_CONTENT", "scroll_y": raw.get("scroll_y", 0)}

    def _observation(self, session, raw):
        check_url(raw["url"], self.allowed)
        observation = {**self._safe_page(raw), "observation_id": str(uuid4()), "elements": []}
        session.elements = {}
        for element in raw["elements"][:100]:
            if element.get("sensitive") or element.get("type") in ("password", "file", "hidden"):
                continue
            reference = str(uuid4())
            session.elements[reference] = deepcopy(element)
            observation["elements"].append({"element_ref": reference, "tag": element["tag"],
                "label": safe_text(element.get("label", ""), 256)})
        session.raw, session.observation = deepcopy(raw), deepcopy(observation)
        return observation

    def _evidence(self, context, session, operation, phase, observation, **extra):
        reference = self.evidence({"schema_version": 1, "trace_id": context["trace_id"],
            "organization_id": context["organization_id"], "session_id": session.session_id if session else None,
            "operation": operation, "phase": phase, "observation": deepcopy(observation), **extra})
        if not isinstance(reference, str) or not reference.strip():
            raise BrowserError("BROWSER_EVIDENCE_UNAVAILABLE")
        return reference

    def verify(self, tool_name, args, output, context):
        return isinstance(output, dict) and output.get("status") == "VERIFIED" and bool(output.get("evidence_ref"))
