from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import pytest
from app.persistence.postgres import tasks
from app.workers.coordinator import WorkerCoordinator
from app.workers.leases import LeaseConfig, RecoveryDecision, WorkerCoordinatorError
from shuliu_runtime.worker.protocol import validate_worker_message
from sqlalchemy import insert, select


def _action_digest(tool_id="foundation.test", arguments=None):
    canonical = json.dumps(
        {"tool_id": tool_id, "arguments": arguments or {"value": "safe"}},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_expired_lease_requires_explicit_safe_reconciliation_and_fences_old_worker(
    worker_harness,
):
    coordinator = worker_harness["coordinator"]
    old_worker = worker_harness["principals"]["worker-old"]
    new_worker = worker_harness["principals"]["worker-new"]
    first = coordinator.lease("task-1", old_worker, idempotency_key="task-1-effect")
    validate_worker_message(worker_harness["transport"].messages[-1][1])

    worker_harness["clock"].advance(timedelta(seconds=35))
    with pytest.raises(WorkerCoordinatorError) as grace_error:
        coordinator.reconcile_expired(
            first.lease_id, decision=RecoveryDecision.RETRY_SAFE
        )
    assert grace_error.value.code == "WORKER_LEASE_GRACE_PERIOD_ACTIVE"

    worker_harness["clock"].advance(timedelta(seconds=6))
    reconciled = coordinator.reconcile_expired(
        first.lease_id, decision=RecoveryDecision.RETRY_SAFE
    )
    assert reconciled.status == "REQUEUED"

    coordinator.heartbeat(new_worker)
    second = coordinator.lease("task-1", new_worker, idempotency_key="task-1-effect")
    assert second.fencing_token > first.fencing_token
    with pytest.raises(WorkerCoordinatorError) as stale_result:
        coordinator.commit_result(
            old_worker,
            lease_id=first.lease_id,
            fencing_token=first.fencing_token,
            idempotency_key="task-1-effect",
            result={"status": "SUCCEEDED", "value": "stale"},
        )
    assert stale_result.value.code == "WORKER_LEASE_FENCED"


def test_unknown_outcome_is_not_automatically_requeued(worker_harness):
    coordinator = worker_harness["coordinator"]
    worker = worker_harness["principals"]["worker-old"]
    lease = coordinator.lease("task-1", worker, idempotency_key="task-1-effect")
    worker_harness["clock"].advance(timedelta(seconds=41))

    result = coordinator.reconcile_expired(
        lease.lease_id, decision=RecoveryDecision.OUTCOME_UNKNOWN
    )

    assert result.status == "RECONCILIATION_REQUIRED"
    with worker_harness["engine"].begin() as connection:
        task = connection.execute(
            select(tasks.c.status).where(tasks.c.task_id == "task-1")
        ).scalar_one()
    assert task == "RECONCILIATION_REQUIRED"
    with pytest.raises(WorkerCoordinatorError) as lease_error:
        coordinator.lease("task-1", worker, idempotency_key="task-1-effect")
    assert lease_error.value.code == "TASK_NOT_READY"


def test_lease_renewal_rejects_a_stale_fencing_token(worker_harness):
    coordinator = worker_harness["coordinator"]
    worker = worker_harness["principals"]["worker-old"]
    lease = coordinator.lease("task-1", worker, idempotency_key="task-1-effect")

    with pytest.raises(WorkerCoordinatorError) as error:
        coordinator.renew(lease.lease_id, worker, fencing_token=lease.fencing_token + 1)

    assert error.value.code == "WORKER_LEASE_FENCED"


def test_worker_tool_dispatch_requires_live_lease_and_current_fencing_generation(worker_harness):
    coordinator = worker_harness["coordinator"]
    worker = worker_harness["principals"]["worker-old"]
    lease = coordinator.lease("task-1", worker, idempotency_key="task-1-effect")

    permit = coordinator.authorize_tool_dispatch(
        worker,
        task_id=lease.task_id,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        idempotency_key=lease.idempotency_key,
        action_digest=_action_digest(),
    )
    assert permit.fencing_token == lease.fencing_token

    worker_harness["clock"].advance(timedelta(seconds=31))
    with pytest.raises(WorkerCoordinatorError) as expired:
        coordinator.authorize_tool_dispatch(
            worker,
            task_id=lease.task_id,
            lease_id=lease.lease_id,
            fencing_token=lease.fencing_token,
            idempotency_key=lease.idempotency_key,
            action_digest=_action_digest(),
        )
    assert expired.value.code == "WORKER_LEASE_EXPIRED"


def test_worker_tool_dispatch_rejects_prior_generation_after_safe_requeue(worker_harness):
    coordinator = worker_harness["coordinator"]
    old_worker = worker_harness["principals"]["worker-old"]
    new_worker = worker_harness["principals"]["worker-new"]
    old = coordinator.lease("task-1", old_worker, idempotency_key="task-1-effect")
    worker_harness["clock"].advance(timedelta(seconds=41))
    coordinator.reconcile_expired(old.lease_id, decision=RecoveryDecision.RETRY_SAFE)
    coordinator.heartbeat(new_worker)
    coordinator.lease("task-1", new_worker, idempotency_key="task-1-effect")

    with pytest.raises(WorkerCoordinatorError) as stale:
        coordinator.authorize_tool_dispatch(
            old_worker,
            task_id=old.task_id,
            lease_id=old.lease_id,
            fencing_token=old.fencing_token,
            idempotency_key=old.idempotency_key,
            action_digest=_action_digest(),
        )
    assert stale.value.code == "WORKER_LEASE_FENCED"


def test_worker_dispatch_permit_is_bound_to_the_exact_cloud_task_tool_and_arguments(worker_harness):
    coordinator = worker_harness["coordinator"]
    worker = worker_harness["principals"]["worker-old"]
    lease = coordinator.lease("task-1", worker, idempotency_key="task-1-effect")

    with pytest.raises(WorkerCoordinatorError) as mismatch:
        coordinator.authorize_tool_dispatch(
            worker,
            task_id=lease.task_id,
            lease_id=lease.lease_id,
            fencing_token=lease.fencing_token,
            idempotency_key=lease.idempotency_key,
            action_digest=_action_digest("foundation.test", {"value": "changed"}),
        )
    assert mismatch.value.code == "WORKER_LEASE_ACTION_MISMATCH"


def test_fencing_tokens_are_unique_within_each_task_and_worker_can_lease_multiple_tasks(worker_harness):
    coordinator = worker_harness["coordinator"]
    worker = worker_harness["principals"]["worker-old"]
    first = coordinator.lease("task-1", worker, idempotency_key="first-effect")
    with worker_harness["engine"].begin() as connection:
        connection.execute(
            insert(tasks).values(
                task_id="task-2",
                organization_id="org-1",
                status="READY",
                payload={"required_capabilities": ["worker.runtime"]},
                created_at=worker_harness["clock"](),
            )
        )

    second = coordinator.lease("task-2", worker, idempotency_key="second-effect")

    assert second.fencing_token == first.fencing_token == 1


def test_lease_capability_matching_fails_closed(worker_harness):
    coordinator = worker_harness["coordinator"]
    worker = worker_harness["principals"]["worker-old"]
    with worker_harness["engine"].begin() as connection:
        connection.execute(
            tasks.update()
            .where(tasks.c.task_id == "task-1")
            .values(payload={"required_capabilities": ["computer.interactive"]})
        )

    with pytest.raises(WorkerCoordinatorError) as error:
        coordinator.lease("task-1", worker, idempotency_key="task-1-effect")

    assert error.value.code == "WORKER_CAPABILITY_MISMATCH"


def test_worker_with_stale_heartbeat_cannot_receive_a_lease(worker_harness):
    coordinator = worker_harness["coordinator"]
    worker = worker_harness["principals"]["worker-old"]
    worker_harness["clock"].advance(timedelta(seconds=16))

    with pytest.raises(WorkerCoordinatorError) as error:
        coordinator.lease("task-1", worker, idempotency_key="task-1-effect")

    assert error.value.code == "WORKER_HEARTBEAT_STALE"


def test_exhausted_safe_attempts_transition_to_dead_letter(worker_harness):
    coordinator = WorkerCoordinator(
        engine=worker_harness["engine"],
        transport=worker_harness["transport"],
        config=LeaseConfig(
            lease_duration=timedelta(seconds=30),
            heartbeat_interval=timedelta(seconds=5),
            grace_period=timedelta(seconds=10),
            max_attempts=1,
        ),
        clock=worker_harness["clock"],
    )
    worker = worker_harness["principals"]["worker-old"]
    lease = coordinator.lease("task-1", worker, idempotency_key="task-1-effect")
    worker_harness["clock"].advance(timedelta(seconds=41))

    outcome = coordinator.reconcile_expired(
        lease.lease_id, decision=RecoveryDecision.RETRY_SAFE
    )

    assert outcome.status == "DEAD_LETTER"
