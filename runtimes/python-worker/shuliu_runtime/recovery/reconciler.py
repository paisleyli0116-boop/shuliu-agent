from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from .checkpoint import Checkpoint, CheckpointStore, IncompatibleCheckpointVersion
from .safe_mode import SafeModeController, SafeModeReason


class RecoveryStatus(str, Enum):
    RESUME = "RESUME"
    WAIT = "WAIT"
    SAFE_MODE = "SAFE_MODE"
    DEAD_LETTER = "DEAD_LETTER"


class ActionReconciliationStatus(str, Enum):
    APPLIED = "APPLIED"
    NOT_APPLIED = "NOT_APPLIED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ActionReconciliation:
    status: ActionReconciliationStatus
    verified_output: Mapping[str, object] | None = None


@dataclass(frozen=True)
class RecoveryDecision:
    status: RecoveryStatus
    checkpoint: Checkpoint | None
    error_code: str | None = None


class ExternalStateReader(Protocol):
    """Read-only adapter for checking whether a pending external action took effect."""

    def inspect(
        self, run_id: str, pending_action: Mapping[str, object]
    ) -> ActionReconciliation: ...


class WorkflowVersionValidator(Protocol):
    def is_compatible(self, checkpoint: Checkpoint) -> bool: ...


class RecoveryCoordinator:
    """Decides whether a checkpoint is safe to resume; it never executes Tools."""

    def __init__(
        self,
        *,
        checkpoint_store: CheckpointStore,
        external_state: ExternalStateReader,
        version_validator: WorkflowVersionValidator,
        safe_mode: SafeModeController,
    ) -> None:
        self._checkpoint_store = checkpoint_store
        self._external_state = external_state
        self._version_validator = version_validator
        self._safe_mode = safe_mode

    def recover(self, run_id: str) -> RecoveryDecision:
        try:
            checkpoint = self._checkpoint_store.latest(run_id)
        except IncompatibleCheckpointVersion:
            return self._enter_safe_mode(
                run_id,
                SafeModeReason.CHECKPOINT_INCOMPATIBLE,
                "CHECKPOINT_SCHEMA_INCOMPATIBLE",
            )
        except Exception:
            return self._enter_safe_mode(
                run_id,
                SafeModeReason.CHECKPOINT_UNAVAILABLE,
                "CHECKPOINT_UNAVAILABLE",
            )

        if checkpoint is None:
            return RecoveryDecision(
                RecoveryStatus.DEAD_LETTER, None, "CHECKPOINT_NOT_FOUND"
            )

        try:
            compatible = self._version_validator.is_compatible(checkpoint)
        except Exception:
            compatible = False
        if compatible is not True:
            self._safe_mode.enter(
                run_id=run_id, reason=SafeModeReason.CONTEXT_INCOMPATIBLE
            )
            return self._finish(
                checkpoint,
                RecoveryStatus.SAFE_MODE,
                "WORKFLOW_CONTEXT_INCOMPATIBLE",
            )

        if checkpoint.pending_approval is not None:
            return self._finish(checkpoint, RecoveryStatus.WAIT, "APPROVAL_PENDING")

        action = checkpoint.pending_action
        if action is None:
            return self._finish(checkpoint, RecoveryStatus.RESUME, None)

        idempotency_key = action.get("idempotency_key")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            return self._finish(
                checkpoint, RecoveryStatus.DEAD_LETTER, "IDEMPOTENCY_KEY_REQUIRED"
            )
        step_id = action.get("step_id")
        if not isinstance(step_id, str) or not step_id.strip():
            return self._finish(
                checkpoint, RecoveryStatus.DEAD_LETTER, "PENDING_ACTION_INVALID"
            )

        try:
            trusted_action = dict(action)
            # Organization scope comes from the validated persisted checkpoint,
            # never from a Tool-supplied pending-action field.
            trusted_action["organization_id"] = checkpoint.organization_id
            reconciliation = self._external_state.inspect(run_id, trusted_action)
        except Exception:
            return self._finish(
                checkpoint, RecoveryStatus.WAIT, "EXTERNAL_STATE_UNAVAILABLE"
            )
        if not isinstance(reconciliation, ActionReconciliation):
            return self._finish(
                checkpoint, RecoveryStatus.WAIT, "EXTERNAL_STATE_INVALID"
            )

        if reconciliation.status is ActionReconciliationStatus.UNKNOWN:
            return self._finish(
                checkpoint, RecoveryStatus.WAIT, "SIDE_EFFECT_STATE_UNKNOWN"
            )
        if reconciliation.status is ActionReconciliationStatus.NOT_APPLIED:
            # The caller may resume through Workflow Engine and Tool Broker with this same key.
            return self._finish(checkpoint, RecoveryStatus.RESUME, None)
        if reconciliation.status is not ActionReconciliationStatus.APPLIED:
            return self._finish(
                checkpoint, RecoveryStatus.WAIT, "EXTERNAL_STATE_INVALID"
            )

        verified_output = reconciliation.verified_output
        if not isinstance(verified_output, Mapping):
            return self._finish(
                checkpoint, RecoveryStatus.WAIT, "APPLIED_RESULT_UNAVAILABLE"
            )
        try:
            next_state = dict(checkpoint.state)
            reconciled_outputs = next_state.get("reconciled_outputs", {})
            if not isinstance(reconciled_outputs, Mapping):
                return self._finish(
                    checkpoint, RecoveryStatus.WAIT, "CHECKPOINT_STATE_INVALID"
                )
            next_outputs = dict(reconciled_outputs)
            next_outputs[step_id] = dict(verified_output)
            next_state["reconciled_outputs"] = next_outputs
            updated = replace(
                checkpoint,
                sequence=checkpoint.sequence + 1,
                state=next_state,
                last_completed_step=step_id,
                pending_action=None,
                created_at=datetime.now(UTC),
            )
            self._checkpoint_store.save(updated)
        except (TypeError, ValueError, OverflowError):
            return self._finish(
                checkpoint, RecoveryStatus.WAIT, "RECONCILED_RESULT_INVALID"
            )
        return self._finish(updated, RecoveryStatus.RESUME, None)

    def _enter_safe_mode(
        self, run_id: str, reason: SafeModeReason, error_code: str
    ) -> RecoveryDecision:
        self._safe_mode.enter(run_id=run_id, reason=reason)
        return RecoveryDecision(RecoveryStatus.SAFE_MODE, None, error_code)

    def _finish(
        self,
        checkpoint: Checkpoint,
        status: RecoveryStatus,
        error_code: str | None,
    ) -> RecoveryDecision:
        self._checkpoint_store.record_decision(
            checkpoint, status=status.value, error_code=error_code
        )
        return RecoveryDecision(status, checkpoint, error_code)
