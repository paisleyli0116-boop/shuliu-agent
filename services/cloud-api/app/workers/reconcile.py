from __future__ import annotations

from app.workers.leases import (
    LeaseConfig,
    PostgresLeaseRepository,
    ReconciliationResult,
    RecoveryDecision,
)


class WorkerLeaseReconciler:
    """Applies an explicit Workflow/Tool reconciliation decision to an expired lease."""

    def __init__(
        self,
        repository: PostgresLeaseRepository,
        config: LeaseConfig,
        clock,
    ) -> None:
        self._repository = repository
        self._config = config
        self._clock = clock

    def reconcile(
        self, lease_id: str, *, decision: RecoveryDecision
    ) -> ReconciliationResult:
        return self._repository.reconcile_expired(
            lease_id,
            decision=decision,
            config=self._config,
            now=self._clock(),
        )
