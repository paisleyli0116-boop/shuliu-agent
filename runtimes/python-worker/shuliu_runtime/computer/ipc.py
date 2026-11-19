from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from shuliu_runtime.tools.broker import ToolExecutionError, ToolResult


_TRACE_ID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
_WINDOW_REF = re.compile(r"(?:0[xX][0-9a-fA-F]{1,16}|[1-9][0-9]{0,19})\Z")
_SAFE_ERROR_PREFIXES = (
    "APPROVAL_",
    "COMPUTER_",
    "IDENTITY_",
    "IDEMPOTENCY_",
    "POLICY_",
    "TOOL_",
)


def create_computer_ipc_handlers(
    runtime: Any,
    *,
    actor: Mapping[str, object],
    organization_id: str,
    privacy: str = "INTERNAL",
) -> dict[str, Any]:
    """Expose read-only Computer Runtime operations with Sidecar-owned identity.

    Identity and organization are captured when the Sidecar builds the handler.
    IPC payloads cannot override them. Mutating computer actions are deliberately
    absent until a production approval lifecycle is connected.
    """
    if not callable(getattr(runtime, "capability", None)) or not callable(
        getattr(runtime, "observe", None)
    ):
        raise TypeError("computer runtime is invalid")
    if (
        not isinstance(actor, Mapping)
        or set(actor) != {"subject_id"}
        or not _safe_identifier(actor.get("subject_id"))
        or not _safe_identifier(organization_id)
    ):
        raise ValueError("trusted computer identity is invalid")
    trusted_actor = {"subject_id": actor["subject_id"], "organization_id": organization_id}

    def capability(params: Mapping[str, object], cancellation) -> dict[str, object]:
        trace_id = _validate_params(params, {"trace_id"})
        if cancellation.is_set():
            return _cancelled(trace_id)
        try:
            result = runtime.capability(
                actor=dict(trusted_actor),
                context=_context(organization_id, trace_id, privacy),
            )
            return _serialize_result(result, trace_id)
        except ToolExecutionError as error:
            return _safe_failure(error.code, trace_id)
        except Exception:
            return _safe_failure("COMPUTER_REQUEST_FAILED", trace_id)

    def observe(params: Mapping[str, object], cancellation) -> dict[str, object]:
        trace_id = _validate_params(params, {"trace_id", "window_ref"})
        window_ref = params.get("window_ref")
        if not isinstance(window_ref, str) or not _WINDOW_REF.fullmatch(window_ref):
            raise ValueError("computer window reference is invalid")
        if cancellation.is_set():
            return _cancelled(trace_id)
        try:
            result = runtime.observe(
                window_ref,
                actor=dict(trusted_actor),
                context=_context(organization_id, trace_id, privacy),
            )
            if cancellation.is_set():
                return _cancelled(trace_id)
            return _serialize_result(result, trace_id)
        except ToolExecutionError as error:
            return _safe_failure(error.code, trace_id)
        except Exception:
            return _safe_failure("COMPUTER_REQUEST_FAILED", trace_id)

    def observe_foreground(params: Mapping[str, object], cancellation) -> dict[str, object]:
        trace_id = _validate_params(params, {"trace_id"})
        if cancellation.is_set():
            return _cancelled(trace_id)
        method = getattr(runtime, "observe_foreground", None)
        if not callable(method):
            return _safe_failure("COMPUTER_WINDOW_UNAVAILABLE", trace_id)
        try:
            result = method(
                actor=dict(trusted_actor),
                context=_context(organization_id, trace_id, privacy),
            )
            if cancellation.is_set():
                return _cancelled(trace_id)
            return _serialize_result(result, trace_id)
        except ToolExecutionError as error:
            return _safe_failure(error.code, trace_id)
        except Exception:
            return _safe_failure("COMPUTER_REQUEST_FAILED", trace_id)

    handlers = {
        "computer.capability": capability,
        "computer.observe": observe,
    }
    if callable(getattr(runtime, "observe_foreground", None)):
        handlers["computer.observe_foreground"] = observe_foreground
    return handlers


def _validate_params(params: object, expected: set[str]) -> str:
    if not isinstance(params, Mapping) or set(params) != expected:
        raise ValueError("computer request shape is invalid")
    trace_id = params.get("trace_id")
    if not isinstance(trace_id, str) or not _TRACE_ID.fullmatch(trace_id):
        raise ValueError("computer trace ID is invalid")
    return str(UUID(trace_id))


def _context(organization_id: str, trace_id: str, privacy: str) -> dict[str, object]:
    return {
        "organization_id": organization_id,
        "trace_id": trace_id,
        "privacy": privacy,
    }


def _serialize_result(result: object, trace_id: str) -> dict[str, object]:
    if not isinstance(result, ToolResult):
        return _safe_failure("COMPUTER_RESULT_INVALID", trace_id)
    if result.verification_status.value != "VERIFIED":
        return _safe_failure(
            result.error_code or "COMPUTER_RESULT_UNVERIFIED", trace_id
        )
    value = {
        "status": "succeeded",
        "tool_id": result.tool_id,
        "tool_version": result.tool_version,
        "verification_status": result.verification_status.value,
        "policy_decision": result.policy_decision.value,
        "policy_version": result.policy_version,
        "policy_rule_id": result.policy_rule_id,
        "output": result.output,
        "trace_id": trace_id,
    }
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        decoded = json.loads(encoded)
    except (TypeError, ValueError):
        return _safe_failure("COMPUTER_RESULT_INVALID", trace_id)
    if not isinstance(decoded, dict):
        return _safe_failure("COMPUTER_RESULT_INVALID", trace_id)
    return decoded


def _safe_failure(code: str, trace_id: str) -> dict[str, object]:
    safe_code = (
        code
        if isinstance(code, str)
        and len(code) <= 128
        and code.isascii()
        and any(code.startswith(prefix) for prefix in _SAFE_ERROR_PREFIXES)
        else "COMPUTER_REQUEST_FAILED"
    )
    return {
        "status": "blocked",
        "safe_error_code": safe_code,
        "trace_id": trace_id,
    }


def _cancelled(trace_id: str) -> dict[str, object]:
    return {
        "status": "cancelled",
        "safe_error_code": "IPC_REQUEST_CANCELLED",
        "trace_id": trace_id,
    }


def _safe_identifier(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 96:
        return False
    return value[0].isalnum() and all(
        char.isascii() and (char.isalnum() or char in "._:-") for char in value
    )
