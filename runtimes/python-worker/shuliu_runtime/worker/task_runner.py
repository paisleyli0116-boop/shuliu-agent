from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from shuliu_runtime.events.observability import redact
from shuliu_runtime.tools.broker import (
    ToolExecutionStatus,
    ToolResult,
    VerificationStatus,
)

from .credentials import WorkerAuthenticationError
from .protocol import WorkerMessage
from .tool_dispatch import WorkerToolBroker


class WorkerTaskRunner:
    """Execute one Cloud-issued Tool task through lease fencing and ToolBroker.

    The Task payload selects only a registered Tool and its JSON arguments. Actor
    identity and the organization scope are taken from the authenticated WebSocket
    session; payload fields cannot supply either value. This runner intentionally
    processes one lease at a time and never retries an execution or ACK.
    """

    def __init__(self, *, connection: Any, tool_broker: WorkerToolBroker, clock=None) -> None:
        if not callable(getattr(connection, "send", None)) or not callable(
            getattr(connection, "dispatch_tool", None)
        ):
            raise TypeError("authenticated Worker connection is required")
        if not callable(getattr(tool_broker, "execute", None)):
            raise TypeError("Tool Broker is required")
        if clock is not None and not callable(clock):
            raise TypeError("Worker task runner clock is invalid")
        self._connection = connection
        self._tool_broker = tool_broker
        self._clock = clock or (lambda: datetime.now(UTC))

    def run_next(self, *, task_id: str, idempotency_key: str) -> WorkerMessage:
        if not _non_empty(task_id) or not _non_empty(idempotency_key):
            raise WorkerAuthenticationError(
                "WORKER_LEASE_REQUEST_INVALID", "Worker lease request is invalid"
            )
        lease = self._connection.send(
            "lease_request",
            {"task_id": task_id, "idempotency_key": idempotency_key},
        )
        if not isinstance(lease, WorkerMessage) or lease.message_type != "lease":
            raise WorkerAuthenticationError(
                "WORKER_LEASE_RESPONSE_INVALID", "Cloud returned no valid lease"
            )
        return self.run_lease(lease)

    def run_lease(self, lease_message: WorkerMessage) -> WorkerMessage:
        if not isinstance(lease_message, WorkerMessage) or lease_message.message_type != "lease":
            raise WorkerAuthenticationError(
                "WORKER_LEASE_RESPONSE_INVALID", "Cloud returned no valid lease"
            )
        payload = lease_message.payload
        task_id = payload.get("task_id")
        lease_id = payload.get("lease_id")
        idempotency_key = payload.get("idempotency_key")
        worker_id = getattr(self._connection, "worker_id", None)
        organization_id = getattr(self._connection, "organization_id", None)
        task = payload.get("task")
        if (
            not _non_empty(task_id)
            or not _non_empty(lease_id)
            or not _non_empty(idempotency_key)
            or not _non_empty(worker_id)
            or not _non_empty(organization_id)
            or not isinstance(task, Mapping)
        ):
            raise WorkerAuthenticationError(
                "WORKER_TASK_PAYLOAD_INVALID", "Leased task is incomplete"
            )

        # Reject task-supplied actor/context fields. Those are locally derived from
        # the verified Cloud authentication response below.
        if any(key in task for key in ("actor", "context", "organization_id", "permissions")):
            raise WorkerAuthenticationError(
                "WORKER_TASK_PAYLOAD_INVALID", "Leased task contains untrusted identity data"
            )
        call = task.get("tool_call")
        if (
            not isinstance(call, Mapping)
            or set(call) != {"tool_id", "arguments"}
            or not _non_empty(call.get("tool_id"))
            or not isinstance(call.get("arguments"), Mapping)
            or not _json_object(call.get("arguments"))
        ):
            raise WorkerAuthenticationError(
                "WORKER_TASK_PAYLOAD_INVALID", "Leased task has no valid Tool call"
            )

        fencing_token = payload.get("fencing_token")
        if isinstance(fencing_token, bool) or not isinstance(fencing_token, int) or fencing_token < 1:
            raise WorkerAuthenticationError(
                "WORKER_LEASE_FENCED", "Leased task generation is invalid"
            )
        lease = dict(payload)
        actor = {"subject_id": worker_id, "organization_id": organization_id}
        context = {
            "organization_id": organization_id,
            "trace_id": lease_message.trace_id,
            "privacy": "INTERNAL",
            "resource": {
                "organization_id": organization_id,
                "resource_type": "task",
                "resource_id": task_id,
            },
        }
        result = self._connection.dispatch_tool(
            lease,
            self._tool_broker,
            tool_id=call["tool_id"],
            args=call["arguments"],
            actor=actor,
            context=context,
            clock=self._clock,
        )
        if not isinstance(result, ToolResult):
            raise WorkerAuthenticationError(
                "WORKER_TOOL_RESULT_INVALID", "Tool Broker returned no verified result"
            )
        if result.execution_status is not ToolExecutionStatus.SUCCEEDED:
            status = "FAILED"
        elif result.verification_status is VerificationStatus.VERIFIED:
            status = "SUCCEEDED"
        else:
            status = "FAILED"

        safe_output = redact(result.output)
        if not _json_value(safe_output):
            safe_output = {"result_unavailable": True}
        result_payload = {
            "task_id": task_id,
            "lease_id": lease_id,
            "idempotency_key": idempotency_key,
            "fencing_token": fencing_token,
            "status": status,
            "result": {
                "tool_id": result.tool_id,
                "tool_version": result.tool_version,
                "verification_status": result.verification_status.value,
                "error_code": result.error_code,
                "policy_decision": result.policy_decision.value,
                "policy_version": result.policy_version,
                "policy_rule_id": result.policy_rule_id,
                "output": safe_output,
            },
        }
        try:
            ack = self._connection.send("result", result_payload)
        except Exception:
            # The Cloud may have committed before the connection failed. Never
            # repeat the Broker call or result send without reconciliation.
            raise WorkerAuthenticationError(
                "WORKER_RESULT_ACK_UNKNOWN", "Cloud result acknowledgement is unknown"
            ) from None
        if (
            not isinstance(ack, WorkerMessage)
            or ack.message_type != "ack"
            or ack.payload.get("task_id") != task_id
            or ack.payload.get("lease_id") != lease_id
            or ack.payload.get("idempotency_key") != idempotency_key
        ):
            raise WorkerAuthenticationError(
                "WORKER_RESULT_ACK_UNKNOWN", "Cloud result acknowledgement did not match"
            )
        return ack


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _json_object(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    return _json_value(value)


def _json_value(value: object) -> bool:
    try:
        json.dumps(value, allow_nan=False)
        return True
    except (TypeError, ValueError):
        return False
