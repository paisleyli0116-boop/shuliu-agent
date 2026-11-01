from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Protocol

from .checkpoint import Checkpoint, CheckpointStore, IncompatibleCheckpointVersion
from .reconciler import RecoveryCoordinator, RecoveryDecision, RecoveryStatus


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_MAX_RESTARTS = 10
_MAX_SECONDS = 300.0


@dataclass(frozen=True)
class RuntimeRestartPolicy:
    """Bounded runtime restart parameters shared by Desktop and Sidecar policy."""

    max_restarts: int
    backoff: tuple[float, ...]
    health_timeout: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_restarts, bool)
            or not isinstance(self.max_restarts, int)
            or self.max_restarts < 0
            or self.max_restarts > _MAX_RESTARTS
        ):
            raise ValueError("max_restarts must be an integer between 0 and 10")
        if not isinstance(self.backoff, tuple) or len(self.backoff) > self.max_restarts:
            raise ValueError("backoff must be a tuple with at most one entry per restart")
        for value in self.backoff:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                or value > _MAX_SECONDS
            ):
                raise ValueError("backoff entries must be finite seconds between 0 and 300")
        if (
            isinstance(self.health_timeout, bool)
            or not isinstance(self.health_timeout, (int, float))
            or not math.isfinite(self.health_timeout)
            or self.health_timeout <= 0
            or self.health_timeout > _MAX_SECONDS
        ):
            raise ValueError("health_timeout must be finite seconds between 0 and 300")

    def backoff_for(self, restart_number: int) -> float:
        if (
            isinstance(restart_number, bool)
            or not isinstance(restart_number, int)
            or restart_number < 1
            or restart_number > self.max_restarts
        ):
            raise ValueError("restart_number is outside the configured restart budget")
        if not self.backoff:
            return 0.0
        return self.backoff[min(restart_number - 1, len(self.backoff) - 1)]


class PreviousProcessFence(Protocol):
    """Proves the prior runtime generation can no longer make progress."""

    def fence_previous(
        self, run_id: str, *, previous_generation: int, current_generation: int
    ) -> bool: ...


class ProcessCrashEventSink(Protocol):
    def record_process_crash(
        self,
        checkpoint: Checkpoint,
        *,
        previous_generation: int,
        current_generation: int,
        status: RecoveryStatus,
        error_code: str | None,
    ) -> None: ...


class ProcessCrashRecovery:
    """Fences the old runtime before delegating to checkpoint reconciliation."""

    def __init__(
        self,
        *,
        recovery: RecoveryCoordinator,
        checkpoint_store: CheckpointStore,
        fence: PreviousProcessFence,
        events: ProcessCrashEventSink,
    ) -> None:
        self._recovery = recovery
        self._checkpoint_store = checkpoint_store
        self._fence = fence
        self._events = events

    def recover_process(
        self, run_id: str, *, previous_generation: int, current_generation: int
    ) -> RecoveryDecision:
        if (
            not isinstance(run_id, str)
            or _SAFE_ID.fullmatch(run_id) is None
            or isinstance(previous_generation, bool)
            or not isinstance(previous_generation, int)
            or previous_generation < 1
            or isinstance(current_generation, bool)
            or not isinstance(current_generation, int)
            or current_generation <= previous_generation
        ):
            return RecoveryDecision(
                RecoveryStatus.SAFE_MODE, None, "RUNTIME_GENERATION_INVALID"
            )

        try:
            checkpoint = self._checkpoint_store.latest(run_id)
        except IncompatibleCheckpointVersion:
            # Let the canonical coordinator record Safe Mode with the
            # schema-specific safe error code; do not reduce it to I/O failure.
            return self._recovery.recover(run_id)
        except Exception:
            return RecoveryDecision(
                RecoveryStatus.SAFE_MODE, None, "CHECKPOINT_UNAVAILABLE"
            )
        if checkpoint is None:
            return self._recovery.recover(run_id)
        if (
            _SAFE_ID.fullmatch(checkpoint.run_id) is None
            or _SAFE_ID.fullmatch(checkpoint.trace_id) is None
            or _SAFE_ID.fullmatch(checkpoint.organization_id) is None
            or checkpoint.run_id != run_id
        ):
            return RecoveryDecision(
                RecoveryStatus.SAFE_MODE, checkpoint, "RECOVERY_METADATA_INVALID"
            )

        try:
            fenced = self._fence.fence_previous(
                run_id,
                previous_generation=previous_generation,
                current_generation=current_generation,
            )
        except Exception:
            return self._record_blocked(
                checkpoint,
                previous_generation,
                current_generation,
                RecoveryStatus.SAFE_MODE,
                "WORKER_LEASE_FENCE_UNAVAILABLE",
            )
        if fenced is not True:
            return self._record_blocked(
                checkpoint,
                previous_generation,
                current_generation,
                RecoveryStatus.SAFE_MODE,
                "WORKER_LEASE_FENCE_UNCONFIRMED",
            )

        try:
            decision = self._recovery.recover(run_id)
        except Exception:
            return self._record_blocked(
                checkpoint,
                previous_generation,
                current_generation,
                RecoveryStatus.SAFE_MODE,
                "PROCESS_RECOVERY_FAILED",
            )
        try:
            self._events.record_process_crash(
                checkpoint,
                previous_generation=previous_generation,
                current_generation=current_generation,
                status=decision.status,
                error_code=decision.error_code,
            )
        except Exception:
            # A recovery decision without durable crash evidence must not resume work.
            return RecoveryDecision(
                RecoveryStatus.SAFE_MODE,
                decision.checkpoint or checkpoint,
                "RECOVERY_AUDIT_UNAVAILABLE",
            )
        return decision

    def _record_blocked(
        self,
        checkpoint: Checkpoint,
        previous_generation: int,
        current_generation: int,
        status: RecoveryStatus,
        error_code: str,
    ) -> RecoveryDecision:
        try:
            self._checkpoint_store.record_decision(
                checkpoint, status=status.value, error_code=error_code
            )
            self._events.record_process_crash(
                checkpoint,
                previous_generation=previous_generation,
                current_generation=current_generation,
                status=status,
                error_code=error_code,
            )
        except Exception:
            error_code = "RECOVERY_AUDIT_UNAVAILABLE"
        return RecoveryDecision(status, checkpoint, error_code)
