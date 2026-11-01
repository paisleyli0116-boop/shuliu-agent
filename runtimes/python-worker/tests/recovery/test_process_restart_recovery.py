from __future__ import annotations

from dataclasses import dataclass

import pytest

from shuliu_runtime.recovery.reconciler import RecoveryDecision, RecoveryStatus
from shuliu_runtime.recovery.checkpoint import IncompatibleCheckpointVersion
from shuliu_runtime.recovery.process_recovery import (
    ProcessCrashRecovery,
    RuntimeRestartPolicy,
)


@dataclass(frozen=True)
class _Checkpoint:
    run_id: str = "run-restart"
    trace_id: str = "trace-restart"
    organization_id: str = "org-restart"


class _Recovery:
    def __init__(self, decision: RecoveryDecision | None = None):
        self.decision = decision or RecoveryDecision(RecoveryStatus.RESUME, _Checkpoint())
        self.calls: list[str] = []

    def recover(self, run_id: str) -> RecoveryDecision:
        self.calls.append(run_id)
        return self.decision


class _Fence:
    def __init__(self, fenced: bool = True):
        self.fenced = fenced
        self.calls: list[tuple[str, int, int]] = []

    def fence_previous(self, run_id: str, *, previous_generation: int, current_generation: int) -> bool:
        self.calls.append((run_id, previous_generation, current_generation))
        return self.fenced


class _Events:
    def __init__(self):
        self.events: list[dict[str, object]] = []

    def record_process_crash(
        self, checkpoint, *, previous_generation, current_generation, status, error_code
    ) -> None:
        self.events.append(
            {
                "event_type": "WORKFLOW_PROCESS_CRASH_RECOVERY",
                "run_id": checkpoint.run_id,
                "trace_id": checkpoint.trace_id,
                "previous_generation": previous_generation,
                "current_generation": current_generation,
                "status": status.value,
                "error_code": error_code,
            }
        )


class _CheckpointStore:
    def __init__(self, checkpoint=None):
        self.checkpoint = checkpoint or _Checkpoint()
        self.decisions = []

    def latest(self, run_id):
        return self.checkpoint

    def record_decision(self, checkpoint, *, status, error_code):
        self.decisions.append((checkpoint.run_id, status, error_code))


def test_runtime_restart_policy_bounds_restarts_and_health_timeout():
    policy = RuntimeRestartPolicy(max_restarts=2, backoff=(0.1, 0.5), health_timeout=3.0)

    assert policy.backoff_for(1) == 0.1
    assert policy.backoff_for(2) == 0.5
    with pytest.raises(ValueError):
        policy.backoff_for(3)
    with pytest.raises(ValueError):
        RuntimeRestartPolicy(max_restarts=-1, backoff=(), health_timeout=3.0)
    with pytest.raises(ValueError):
        RuntimeRestartPolicy(max_restarts=1, backoff=(-0.1,), health_timeout=3.0)
    with pytest.raises(ValueError):
        RuntimeRestartPolicy(max_restarts=1, backoff=(), health_timeout=0)


def test_process_recovery_fences_previous_generation_before_reconciling():
    recovery = _Recovery()
    fence = _Fence(fenced=False)
    events = _Events()
    store = _CheckpointStore()
    coordinator = ProcessCrashRecovery(
        recovery=recovery, checkpoint_store=store, fence=fence, events=events
    )

    result = coordinator.recover_process(
        "run-restart", previous_generation=4, current_generation=5
    )

    assert result.status is RecoveryStatus.SAFE_MODE
    assert result.error_code == "WORKER_LEASE_FENCE_UNCONFIRMED"
    assert recovery.calls == []
    assert fence.calls == [("run-restart", 4, 5)]
    assert store.decisions == [("run-restart", "SAFE_MODE", "WORKER_LEASE_FENCE_UNCONFIRMED")]
    assert events.events == [
        {
            "event_type": "WORKFLOW_PROCESS_CRASH_RECOVERY",
            "run_id": "run-restart",
            "trace_id": "trace-restart",
            "previous_generation": 4,
            "current_generation": 5,
            "status": "SAFE_MODE",
            "error_code": "WORKER_LEASE_FENCE_UNCONFIRMED",
        }
    ]


def test_process_recovery_rejects_untrusted_audit_organization_before_fencing():
    recovery = _Recovery()
    fence = _Fence()
    events = _Events()
    store = _CheckpointStore(_Checkpoint(organization_id="org/../../private"))
    coordinator = ProcessCrashRecovery(
        recovery=recovery, checkpoint_store=store, fence=fence, events=events
    )

    result = coordinator.recover_process(
        "run-restart", previous_generation=4, current_generation=5
    )

    assert result.status is RecoveryStatus.SAFE_MODE
    assert result.error_code == "RECOVERY_METADATA_INVALID"
    assert recovery.calls == []
    assert fence.calls == []
    assert events.events == []


def test_process_recovery_does_not_resume_pending_approval():
    recovery = _Recovery(RecoveryDecision(RecoveryStatus.WAIT, _Checkpoint(), "APPROVAL_PENDING"))
    events = _Events()
    coordinator = ProcessCrashRecovery(
        recovery=recovery, checkpoint_store=_CheckpointStore(), fence=_Fence(), events=events
    )

    result = coordinator.recover_process(
        "run-restart", previous_generation=4, current_generation=5
    )

    assert result.status is RecoveryStatus.WAIT
    assert result.error_code == "APPROVAL_PENDING"
    assert recovery.calls == ["run-restart"]
    assert events.events[0]["trace_id"] == "trace-restart"


def test_process_recovery_fails_closed_when_crash_event_cannot_be_persisted():
    recovery = _Recovery()

    class UnavailableEvents(_Events):
        def record_process_crash(self, *args, **kwargs):
            raise OSError("private storage path and input")

    coordinator = ProcessCrashRecovery(
        recovery=recovery,
        checkpoint_store=_CheckpointStore(),
        fence=_Fence(),
        events=UnavailableEvents(),
    )

    result = coordinator.recover_process(
        "run-restart", previous_generation=4, current_generation=5
    )

    assert result.status is RecoveryStatus.SAFE_MODE
    assert result.error_code == "RECOVERY_AUDIT_UNAVAILABLE"
    assert recovery.calls == ["run-restart"]
    assert "private storage" not in str(result)


def test_process_recovery_preserves_incompatible_checkpoint_version_error():
    class IncompatibleStore(_CheckpointStore):
        def latest(self, run_id):
            raise IncompatibleCheckpointVersion("schema mismatch details stay private")

    recovery = _Recovery(
        RecoveryDecision(
            RecoveryStatus.SAFE_MODE,
            None,
            "CHECKPOINT_SCHEMA_INCOMPATIBLE",
        )
    )
    coordinator = ProcessCrashRecovery(
        recovery=recovery,
        checkpoint_store=IncompatibleStore(),
        fence=_Fence(),
        events=_Events(),
    )

    result = coordinator.recover_process(
        "run-restart", previous_generation=4, current_generation=5
    )

    assert result.status is RecoveryStatus.SAFE_MODE
    assert result.error_code == "CHECKPOINT_SCHEMA_INCOMPATIBLE"
    assert recovery.calls == ["run-restart"]


@pytest.mark.parametrize(
    ("previous_generation", "current_generation"),
    [(0, 1), (2, 2), (4, 3), (-1, 1)],
)
def test_process_recovery_rejects_invalid_generation_transition(
    previous_generation: int, current_generation: int
):
    recovery = _Recovery()
    fence = _Fence()
    coordinator = ProcessCrashRecovery(
        recovery=recovery,
        checkpoint_store=_CheckpointStore(),
        fence=fence,
        events=_Events(),
    )

    result = coordinator.recover_process(
        "run-restart",
        previous_generation=previous_generation,
        current_generation=current_generation,
    )

    assert result.status is RecoveryStatus.SAFE_MODE
    assert result.error_code == "RUNTIME_GENERATION_INVALID"
    assert recovery.calls == []
    assert fence.calls == []
