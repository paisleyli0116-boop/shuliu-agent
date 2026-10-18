from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol
from uuid import uuid4

from app.workers.leases import LeaseGrant, WorkerCoordinatorError, WorkerResultAck


class WorkerTransport(Protocol):
    """Transport port. Production WSS connection/authentication is owned by Cloud API."""

    def send(self, worker_id: str, message: dict[str, Any]) -> None: ...


def lease_message(grant: LeaseGrant, *, trace_id: str | None = None) -> dict[str, Any]:
    return {
        "protocol_name": "worker",
        "protocol_version": 1,
        "message_id": str(uuid4()),
        "request_id": str(uuid4()),
        "trace_id": trace_id or str(uuid4()),
        "message_type": "lease",
        "payload": {
            "task_id": grant.task_id,
            "lease_id": grant.lease_id,
            "fencing_token": grant.fencing_token,
            "idempotency_key": grant.idempotency_key,
            "expires_at": grant.expires_at.isoformat(),
            "task": dict(grant.task_payload),
        },
    }


def ack_message(ack: WorkerResultAck, *, trace_id: str | None = None) -> dict[str, Any]:
    return {
        "protocol_name": "worker",
        "protocol_version": 1,
        "message_id": str(uuid4()),
        "request_id": str(uuid4()),
        "trace_id": trace_id or str(uuid4()),
        "message_type": "ack",
        "payload": {
            "task_id": ack.task_id,
            "lease_id": ack.lease_id,
            "idempotency_key": ack.idempotency_key,
        },
    }


def send_message(
    transport: WorkerTransport, worker_id: str, message: Mapping[str, Any]
) -> None:
    try:
        transport.send(worker_id, dict(message))
    except Exception:  # noqa: BLE001 - transport exceptions leave delivery ambiguous
        # Delivery may have succeeded before the connection failed. The caller must not
        # release the lease or retry a potentially side-effecting task automatically.
        raise WorkerCoordinatorError(
            "WORKER_DISPATCH_OUTCOME_UNKNOWN",
            "Worker message delivery outcome is unknown",
        ) from None
