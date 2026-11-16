from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from shuliu_runtime.persistence.sqlite import LOCAL_MIGRATIONS, SQLiteDatabase
from shuliu_runtime.persistence.sqlite_idempotency import SQLiteIdempotencyStore
from shuliu_runtime.recovery.checkpoint import Checkpoint, CheckpointStore
from shuliu_runtime.recovery.generation_fence import SQLiteRuntimeGenerationFence
from shuliu_runtime.recovery.idempotency_state import IdempotencyExternalStateReader
from shuliu_runtime.recovery.process_recovery import ProcessCrashRecovery
from shuliu_runtime.recovery.reconciler import RecoveryCoordinator, RecoveryStatus
from shuliu_runtime.recovery.safe_mode import SafeModeReason
from shuliu_runtime.recovery.sqlite_safe_mode import SQLiteSafeModeController
from shuliu_runtime.workflow.sqlite_repository import SQLiteWorkflowRepository


class _WorkflowVersionValidator:
    def __init__(self, expected_context_version: str) -> None:
        if not isinstance(expected_context_version, str) or not expected_context_version.strip():
            raise ValueError("workflow context version is required")
        self._expected = expected_context_version

    def is_compatible(self, checkpoint: Checkpoint) -> bool:
        return checkpoint.context_version == self._expected


class _AuthorizedTransitionFence:
    def __init__(self, fence: SQLiteRuntimeGenerationFence, transition_id: str) -> None:
        self._fence = fence
        self._transition_id = transition_id

    def fence_previous(
        self,
        run_id: str,
        *,
        previous_generation: int,
        current_generation: int,
    ) -> bool:
        return self._fence.confirm_transition(
            previous_generation=previous_generation,
            current_generation=current_generation,
            transition_id=self._transition_id,
        )


def create_process_recovery_ipc_handlers(
    database: SQLiteDatabase,
    *,
    expected_context_version: str = "workflow:v1;skill:v1",
) -> dict[str, Any]:
    """Create the Sidecar's production recovery query and decision handlers."""
    checkpoint_store = CheckpointStore(database)
    workflow_repository = SQLiteWorkflowRepository(database)
    generation_fence = SQLiteRuntimeGenerationFence(database)
    idempotency = SQLiteIdempotencyStore(database)
    recovery_lock = threading.RLock()
    active_transition: tuple[int, int, str] | None = None
    recovered_runs: dict[str, tuple[str, str]] = {}

    def begin(params: Mapping[str, Any], _cancelled: threading.Event) -> dict[str, object]:
        nonlocal active_transition, recovered_runs
        transition_id = params.get("transition_id")
        if set(params) != {"transition_id"} or not _valid_transition_id(transition_id):
            return {"status": RecoveryStatus.SAFE_MODE.value, "error_code": "RUNTIME_GENERATION_INVALID"}
        with recovery_lock:
            try:
                checkpoints = checkpoint_store.latest_all()
                checkpoint_run_ids = {checkpoint.run_id for checkpoint in checkpoints}
                unfinished_runs = workflow_repository.list_unfinished()
                uncheckpointed_runs = [
                    run for run in unfinished_runs if run.run_id not in checkpoint_run_ids
                ]
                if uncheckpointed_runs:
                    safe_mode = SQLiteSafeModeController(database)
                    for run in uncheckpointed_runs:
                        safe_mode.enter(
                            run_id=run.run_id,
                            reason=SafeModeReason.CHECKPOINT_UNAVAILABLE,
                        )
                    return {
                        "status": RecoveryStatus.SAFE_MODE.value,
                        "error_code": "CHECKPOINT_NOT_FOUND",
                    }
                generations = generation_fence.begin_transition(
                    transition_id=transition_id,
                    has_prior_checkpoints=bool(checkpoints or unfinished_runs),
                )
            except Exception:
                generations = None
            if generations is not None:
                previous, current = generations
                active_transition = (previous, current, transition_id)
                recovered_runs = {}
        if generations is None:
            return {
                "status": RecoveryStatus.SAFE_MODE.value,
                "error_code": "WORKER_LEASE_FENCE_UNCONFIRMED",
            }
        return {
            "status": RecoveryStatus.RESUME.value,
            "error_code": None,
            "previous_generation": previous,
            "current_generation": current,
            "transition_id": transition_id,
        }

    def list_checkpoints(
        _params: Mapping[str, Any], _cancelled: threading.Event
    ) -> dict[str, object]:
        with recovery_lock:
            checkpoints = checkpoint_store.latest_all()
        return {
            "checkpoints": [
                {
                    "run_id": checkpoint.run_id,
                    "sequence": checkpoint.sequence,
                    "has_pending_action": checkpoint.pending_action is not None,
                    "has_pending_approval": checkpoint.pending_approval is not None,
                    "context_version": checkpoint.context_version,
                }
                for checkpoint in checkpoints
            ]
        }

    def recover_run(
        params: Mapping[str, Any], cancelled: threading.Event
    ) -> dict[str, object]:
        run_id = params.get("run_id")
        previous = params.get("previous_generation")
        current = params.get("current_generation")
        transition_id = params.get("transition_id")
        if (
            not isinstance(run_id, str)
            or not _valid_generation_pair(previous, current)
            or not _valid_transition_id(transition_id)
        ):
            return {"status": RecoveryStatus.SAFE_MODE.value, "error_code": "RUNTIME_GENERATION_INVALID"}
        if cancelled.is_set():
            return {"status": RecoveryStatus.SAFE_MODE.value, "error_code": "PROCESS_RECOVERY_FAILED"}
        with recovery_lock:
            if active_transition != (previous, current, transition_id):
                return {
                    "status": RecoveryStatus.SAFE_MODE.value,
                    "error_code": "WORKER_LEASE_FENCE_UNCONFIRMED",
                }
            coordinator = RecoveryCoordinator(
                checkpoint_store=checkpoint_store,
                external_state=IdempotencyExternalStateReader(idempotency),
                version_validator=_WorkflowVersionValidator(expected_context_version),
                safe_mode=SQLiteSafeModeController(database),
            )
            process_recovery = ProcessCrashRecovery(
                recovery=coordinator,
                checkpoint_store=checkpoint_store,
                fence=_AuthorizedTransitionFence(generation_fence, transition_id),
                events=checkpoint_store,
            )
            decision = process_recovery.recover_process(
                run_id,
                previous_generation=previous,
                current_generation=current,
            )
            if decision.status is RecoveryStatus.RESUME:
                checkpoint = checkpoint_store.latest(run_id)
                if checkpoint is not None:
                    recovered_runs[run_id] = (
                        checkpoint.organization_id,
                        checkpoint.trace_id,
                    )
        return {"status": decision.status.value, "error_code": decision.error_code}

    def complete(params: Mapping[str, Any], cancelled: threading.Event) -> dict[str, object]:
        nonlocal active_transition, recovered_runs
        if (
            set(params) != {"transition_id", "current_generation"}
            or not _valid_transition_id(params.get("transition_id"))
            or isinstance(params.get("current_generation"), bool)
            or not isinstance(params.get("current_generation"), int)
            or params["current_generation"] < 1
        ):
            return {"status": RecoveryStatus.SAFE_MODE.value, "error_code": "RUNTIME_GENERATION_INVALID"}
        if cancelled.is_set():
            return {"status": RecoveryStatus.SAFE_MODE.value, "error_code": "PROCESS_RECOVERY_FAILED"}
        transition_id = params["transition_id"]
        current_generation = params["current_generation"]
        with recovery_lock:
            transition = active_transition
            if (
                transition is None
                or transition[1] != current_generation
                or transition[2] != transition_id
                or not generation_fence.is_current_transition(
                    current_generation=current_generation,
                    transition_id=transition_id,
                )
            ):
                return {"status": RecoveryStatus.SAFE_MODE.value, "error_code": "WORKER_LEASE_FENCE_UNCONFIRMED"}
            try:
                checkpoints = checkpoint_store.latest_all()
                if any(checkpoint.run_id not in recovered_runs for checkpoint in checkpoints):
                    return {"status": RecoveryStatus.SAFE_MODE.value, "error_code": "SAFE_MODE_RELEASE_NOT_READY"}
                unfinished_runs = workflow_repository.list_unfinished()
                if any(run.run_id not in recovered_runs for run in unfinished_runs):
                    return {"status": RecoveryStatus.SAFE_MODE.value, "error_code": "SAFE_MODE_RELEASE_NOT_READY"}
                persisted_safe_mode = SQLiteSafeModeController(database).status()
                if persisted_safe_mode.error_code == "RUNTIME_SAFE_MODE_STATE_INVALID":
                    return {"status": RecoveryStatus.SAFE_MODE.value, "error_code": persisted_safe_mode.error_code}
                if any(run_id not in recovered_runs for run_id in persisted_safe_mode.run_ids):
                    return {"status": RecoveryStatus.SAFE_MODE.value, "error_code": "SAFE_MODE_RELEASE_NOT_READY"}
                with database.connect() as connection:
                    integrity = connection.execute("PRAGMA quick_check").fetchone()
                    applied = {
                        row["migration_id"]
                        for row in connection.execute("SELECT migration_id FROM schema_migrations")
                    }
                if integrity is None or integrity[0] != "ok" or not {
                    migration.migration_id for migration in LOCAL_MIGRATIONS
                }.issubset(applied):
                    return {"status": RecoveryStatus.SAFE_MODE.value, "error_code": "DATABASE_SCHEMA_INCOMPATIBLE"}
                if not SQLiteSafeModeController(database).release_after_verified_recovery(
                    recovered=recovered_runs,
                    transition_id=transition_id,
                    current_generation=current_generation,
                    cancelled=cancelled,
                ):
                    error_code = (
                        "PROCESS_RECOVERY_FAILED"
                        if cancelled.is_set()
                        else "SAFE_MODE_RELEASE_NOT_READY"
                    )
                    return {"status": RecoveryStatus.SAFE_MODE.value, "error_code": error_code}
            except Exception:
                return {"status": RecoveryStatus.SAFE_MODE.value, "error_code": "PROCESS_RECOVERY_FAILED"}
            active_transition = None
            recovered_runs = {}
        return {"status": RecoveryStatus.RESUME.value, "error_code": None}

    return {
        "runtime.recovery.begin": begin,
        "runtime.recovery.checkpoints": list_checkpoints,
        "runtime.recovery.recover_run": recover_run,
        "runtime.recovery.complete": complete,
    }


def _valid_generation_pair(previous: object, current: object) -> bool:
    return (
        isinstance(previous, int)
        and not isinstance(previous, bool)
        and previous >= 1
        and isinstance(current, int)
        and not isinstance(current, bool)
        and current == previous + 1
    )


def _valid_transition_id(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False
