from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy

from shuliu_runtime.policy.rules import PolicyOutcome
from shuliu_runtime.tools.broker import (
    ToolExecutionError,
    ToolExecutionStatus,
    ToolResult,
    VerificationStatus,
)


class SkillCapabilityProxy:
    """The only Skill-to-Foundation bridge; identity and approval context stay host-owned."""

    def __init__(
        self,
        *,
        broker: object,
        registry: object,
        actor: Mapping[str, object],
        context: Mapping[str, object],
        approved_permissions: tuple[str, ...],
        idempotency_key: str,
    ) -> None:
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("a host-generated idempotency key is required")
        if not callable(getattr(broker, "execute", None)):
            raise TypeError("a Tool Broker is required")
        if not callable(getattr(registry, "get", None)):
            raise TypeError("a Tool permission registry is required")
        self._broker = broker
        self.registry = registry
        self._actor = deepcopy(dict(actor))
        self._context = deepcopy(dict(context))
        self._approved_permissions = frozenset(approved_permissions)
        self._idempotency_key = idempotency_key

    @property
    def approved_permissions(self) -> frozenset[str]:
        return self._approved_permissions

    def dispatch(self, frame: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(frame, Mapping):
            raise TypeError("Skill capability frame must be an object")
        if set(frame) != {"type", "request_id", "tool_id", "args"}:
            raise ValueError("Skill capability frame has unsupported fields")
        if frame.get("type") != "tool.request":
            raise ValueError("Skill may request only Tool Broker capabilities")
        request_id = frame.get("request_id")
        tool_id = frame.get("tool_id")
        args = frame.get("args")
        if (
            not isinstance(request_id, str)
            or not request_id.strip()
            or not isinstance(tool_id, str)
            or not tool_id.strip()
            or not isinstance(args, Mapping)
        ):
            raise ValueError("Skill capability request is malformed")

        try:
            definition = self.registry.get(tool_id)
            permissions = getattr(definition, "permissions", None)
            if not isinstance(permissions, (tuple, list)) or not set(
                permissions
            ).issubset(self._approved_permissions):
                return _denied(request_id, "SKILL_PERMISSION_DENIED")
        except Exception:  # noqa: BLE001 - registry errors deny capability calls
            return _denied(request_id, "SKILL_PERMISSION_DENIED")

        key = _call_idempotency_key(self._idempotency_key, tool_id, args)
        try:
            result = self._broker.execute(
                tool_id,
                dict(args),
                deepcopy(self._actor),
                deepcopy(self._context),
                idempotency_key=key,
            )
        except ToolExecutionError as error:
            return _denied(request_id, error.code)
        except Exception:  # noqa: BLE001 - unexpected broker failures fail closed
            return _denied(request_id, "TOOL_BROKER_UNAVAILABLE")

        if not isinstance(result, ToolResult):
            return _denied(request_id, "TOOL_RESULT_INVALID")
        if result.policy_decision is PolicyOutcome.DENY:
            return _denied(request_id, "POLICY_DENIED")
        if result.execution_status is not ToolExecutionStatus.SUCCEEDED:
            return _denied(request_id, result.error_code or "TOOL_EXECUTION_FAILED")
        if result.verification_status is not VerificationStatus.VERIFIED:
            return _denied(request_id, result.error_code or "TOOL_RESULT_UNVERIFIED")
        output = result.output
        try:
            json.dumps(output, allow_nan=False)
        except (TypeError, ValueError):
            return _denied(request_id, "TOOL_OUTPUT_INVALID")
        return {
            "type": "tool.response",
            "request_id": request_id,
            "output": output,
        }


def _call_idempotency_key(
    run_key: str, tool_id: str, args: Mapping[str, object]
) -> str:
    canonical = json.dumps(
        {"tool_id": tool_id, "args": args},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    digest = hashlib.sha256(f"{run_key}\0{canonical}".encode()).hexdigest()
    return f"skill:{digest}"


def _denied(request_id: str, code: str) -> dict[str, object]:
    return {
        "type": "tool.response",
        "request_id": request_id,
        "error": {"code": code},
    }
