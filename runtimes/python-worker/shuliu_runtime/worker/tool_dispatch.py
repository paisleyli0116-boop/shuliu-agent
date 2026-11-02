from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .credentials import WorkerAuthenticationError


@dataclass(frozen=True)
class WorkerDispatchAuthorization:
    task_id: str
    lease_id: str
    fencing_token: int
    idempotency_key: str
    expires_at: datetime


class WorkerLeaseAuthorizer(Protocol):
    def authorize_tool_dispatch(
        self,
        *,
        task_id: str,
        lease_id: str,
        fencing_token: int,
        idempotency_key: str,
    ) -> WorkerDispatchAuthorization: ...


class WorkerToolBroker(Protocol):
    def execute(
        self,
        tool_id: str,
        args: Mapping[str, object],
        actor: Mapping[str, object],
        context: Mapping[str, object],
        *,
        idempotency_key: str | None,
    ) -> Any: ...


class WorkerToolDispatcher:
    """Require a matching, unexpired Cloud authorization immediately before Broker dispatch."""

    def __init__(
        self,
        authorizer: WorkerLeaseAuthorizer,
        tool_broker: WorkerToolBroker,
        *,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        if not callable(getattr(authorizer, "authorize_tool_dispatch", None)):
            raise TypeError("Worker lease authorizer is required")
        if not callable(getattr(tool_broker, "execute", None)):
            raise TypeError("Tool Broker is required")
        if not callable(clock):
            raise TypeError("Worker dispatch clock is required")
        self._authorizer = authorizer
        self._tool_broker = tool_broker
        self._clock = clock

    def execute(
        self,
        lease: Mapping[str, object],
        *,
        tool_id: str,
        args: Mapping[str, object],
        actor: Mapping[str, object],
        context: Mapping[str, object],
    ) -> Any:
        if not isinstance(lease, Mapping):
            raise ValueError("Worker lease is required")
        task_id = _required_text(lease.get("task_id"))
        lease_id = _required_text(lease.get("lease_id"))
        idempotency_key = _required_text(lease.get("idempotency_key"))
        fencing_token = lease.get("fencing_token")
        if (
            isinstance(fencing_token, bool)
            or not isinstance(fencing_token, int)
            or fencing_token < 1
        ):
            raise ValueError("Worker lease fencing token is invalid")
        if not _required_text(tool_id) or not isinstance(args, Mapping) or not isinstance(actor, Mapping) or not isinstance(context, Mapping):
            raise ValueError("Worker Tool dispatch input is invalid")

        authorization = self._authorizer.authorize_tool_dispatch(
            task_id=task_id,
            lease_id=lease_id,
            fencing_token=fencing_token,
            idempotency_key=idempotency_key,
        )
        if not isinstance(authorization, WorkerDispatchAuthorization) or (
            authorization.task_id != task_id
            or authorization.lease_id != lease_id
            or authorization.fencing_token != fencing_token
            or authorization.idempotency_key != idempotency_key
            or not isinstance(authorization.expires_at, datetime)
            or authorization.expires_at.tzinfo is None
            or authorization.expires_at.utcoffset() is None
        ):
            raise WorkerAuthenticationError(
                "WORKER_LEASE_AUTHORIZATION_INVALID",
                "Cloud did not return a matching Tool dispatch authorization",
            )
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise WorkerAuthenticationError(
                "WORKER_LEASE_AUTHORIZATION_INVALID", "Worker dispatch clock is invalid"
            )
        if authorization.expires_at.astimezone(UTC) <= now.astimezone(UTC):
            raise WorkerAuthenticationError(
                "WORKER_LEASE_EXPIRED", "Cloud lease authorization expired before dispatch"
            )

        broker_context = dict(context)
        broker_context["worker_lease"] = {
            "task_id": task_id,
            "lease_id": lease_id,
            "fencing_token": fencing_token,
            "idempotency_key": idempotency_key,
            "authorization_expires_at": authorization.expires_at.astimezone(UTC).isoformat(),
        }
        return self._tool_broker.execute(
            tool_id,
            args,
            actor,
            broker_context,
            idempotency_key=idempotency_key,
        )


def _required_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Worker lease identity is invalid")
    return value
