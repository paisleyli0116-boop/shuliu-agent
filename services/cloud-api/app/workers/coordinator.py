from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.workers.dispatch import (
    WorkerTransport,
    ack_message,
    lease_message,
    send_message,
)
from app.workers.leases import (
    HeartbeatReceipt,
    LeaseConfig,
    LeaseGrant,
    PostgresLeaseRepository,
    ReconciliationResult,
    RecoveryDecision,
    WorkerCoordinatorError,
    WorkerDispatchPermit,
    WorkerPrincipal,
    WorkerResultAck,
)
from app.workers.reconcile import WorkerLeaseReconciler
from sqlalchemy.engine import Engine


class WorkerCoordinator:
    def __init__(
        self,
        *,
        engine: Engine,
        transport: WorkerTransport,
        config: LeaseConfig,
        clock=None,
    ) -> None:
        self._repository = PostgresLeaseRepository(engine)
        self._transport = transport
        self._config = config
        self._clock = clock or (lambda: datetime.now(UTC))
        self._reconciler = WorkerLeaseReconciler(self._repository, config, self._clock)

    def record_authenticated_worker(
        self,
        principal: WorkerPrincipal,
        *,
        worker_version: str,
        protocol_version: int,
        capabilities: tuple[str, ...],
    ) -> None:
        """Persist public registration metadata after the connection layer authenticates."""
        self._repository.record_authenticated_worker(
            principal,
            worker_version=worker_version,
            protocol_version=protocol_version,
            capabilities=capabilities,
            now=self._clock(),
        )

    def heartbeat(self, principal: WorkerPrincipal) -> HeartbeatReceipt:
        return self._repository.heartbeat(principal, now=self._clock())

    def disconnected(self, principal: WorkerPrincipal) -> None:
        self._repository.disconnected(principal)

    def lease(
        self,
        task_id: str,
        principal: WorkerPrincipal,
        *,
        idempotency_key: str,
        trace_id: str | None = None,
    ) -> LeaseGrant:
        grant = self._repository.issue_lease(
            task_id,
            principal,
            idempotency_key=idempotency_key,
            config=self._config,
            now=self._clock(),
        )
        send_message(
            self._transport,
            principal.worker_id,
            lease_message(grant, trace_id=trace_id),
        )
        return grant

    def renew(
        self,
        lease_id: str,
        principal: WorkerPrincipal,
        *,
        fencing_token: int,
    ) -> LeaseGrant:
        return self._repository.renew(
            lease_id,
            principal,
            fencing_token=fencing_token,
            config=self._config,
            now=self._clock(),
        )

    def authorize_tool_dispatch(
        self,
        principal: WorkerPrincipal,
        *,
        task_id: str,
        lease_id: str,
        fencing_token: int,
        idempotency_key: str,
        action_digest: str,
    ) -> WorkerDispatchPermit:
        return self._repository.authorize_tool_dispatch(
            principal,
            task_id=task_id,
            lease_id=lease_id,
            fencing_token=fencing_token,
            idempotency_key=idempotency_key,
            action_digest=action_digest,
            config=self._config,
            now=self._clock(),
        )

    def reconcile_expired(
        self, lease_id: str, *, decision: RecoveryDecision
    ) -> ReconciliationResult:
        return self._reconciler.reconcile(lease_id, decision=decision)

    def commit_result(
        self,
        principal: WorkerPrincipal,
        *,
        lease_id: str,
        fencing_token: int,
        idempotency_key: str,
        result: Mapping[str, Any],
        trace_id: str | None = None,
    ) -> WorkerResultAck:
        ack = self._repository.commit_result(
            principal,
            lease_id=lease_id,
            fencing_token=fencing_token,
            idempotency_key=idempotency_key,
            result=result,
            now=self._clock(),
        )
        try:
            self._transport.send(
                principal.worker_id, ack_message(ack, trace_id=trace_id)
            )
        except Exception:  # noqa: BLE001 - transport exceptions leave delivery ambiguous
            # The event and state transition are durable; the Worker can safely retry its
            # result message and receive the same ACK from the idempotency ledger.
            raise WorkerCoordinatorError(
                "WORKER_ACK_OUTCOME_UNKNOWN", "Worker ACK delivery outcome is unknown"
            ) from None
        return ack
