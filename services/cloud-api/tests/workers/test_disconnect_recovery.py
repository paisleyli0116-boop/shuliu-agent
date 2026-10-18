from __future__ import annotations

from datetime import timedelta

import pytest
from app.persistence.postgres import tasks, workers
from app.workers.leases import (
    RecoveryDecision,
    WorkerCoordinatorError,
    WorkerPrincipal,
)
from sqlalchemy import select


def test_heartbeat_updates_persisted_worker_health(worker_harness):
    coordinator = worker_harness["coordinator"]
    worker = worker_harness["principals"]["worker-old"]
    worker_harness["clock"].advance(timedelta(seconds=3))

    heartbeat = coordinator.heartbeat(worker)

    assert heartbeat.worker_id == worker.worker_id
    with worker_harness["engine"].begin() as connection:
        row = connection.execute(
            select(workers.c.last_heartbeat_at, workers.c.status).where(
                workers.c.worker_id == worker.worker_id
            )
        ).one()
    assert row.status == "ONLINE"
    assert (
        row.last_heartbeat_at.replace(tzinfo=worker_harness["clock"]().tzinfo)
        == worker_harness["clock"]()
    )


def test_authenticated_registration_persists_only_non_secret_worker_metadata(
    worker_harness,
):
    coordinator = worker_harness["coordinator"]
    registered_worker = WorkerPrincipal(
        worker_id="worker-registered", organization_id="org-1"
    )

    coordinator.record_authenticated_worker(
        registered_worker,
        worker_version="0.1.0",
        protocol_version=1,
        capabilities=("worker.runtime", "tool.execute"),
    )

    with worker_harness["engine"].begin() as connection:
        row = (
            connection.execute(
                select(workers).where(
                    workers.c.worker_id == registered_worker.worker_id
                )
            )
            .mappings()
            .one()
        )
    assert row["capabilities"] == ["worker.runtime", "tool.execute"]
    assert row["worker_version"] == "0.1.0"
    assert row["protocol_version"] == 1
    assert not {
        "registration_token",
        "session_credential",
        "device_secret",
    }.intersection(row.keys())


def test_reconnect_heartbeat_restores_online_state_without_reissuing_lease(
    worker_harness,
):
    coordinator = worker_harness["coordinator"]
    worker = worker_harness["principals"]["worker-old"]
    lease = coordinator.lease("task-1", worker, idempotency_key="task-1-effect")
    coordinator.disconnected(worker)

    coordinator.heartbeat(worker)
    renewed = coordinator.renew(
        lease.lease_id, worker, fencing_token=lease.fencing_token
    )

    assert renewed.lease_id == lease.lease_id
    assert renewed.fencing_token == lease.fencing_token


def test_disconnect_waits_through_lease_and_grace_before_reconciliation(worker_harness):
    coordinator = worker_harness["coordinator"]
    worker = worker_harness["principals"]["worker-old"]
    lease = coordinator.lease("task-1", worker, idempotency_key="task-1-effect")
    coordinator.disconnected(worker)
    worker_harness["clock"].advance(timedelta(seconds=35))

    with pytest.raises(WorkerCoordinatorError) as error:
        coordinator.reconcile_expired(
            lease.lease_id, decision=RecoveryDecision.RETRY_SAFE
        )

    assert error.value.code == "WORKER_LEASE_GRACE_PERIOD_ACTIVE"
    with worker_harness["engine"].begin() as connection:
        status = connection.execute(
            select(tasks.c.status).where(tasks.c.task_id == "task-1")
        ).scalar_one()
    assert status == "LEASED"


def test_transport_failure_does_not_release_or_redispatch_ambiguous_lease(
    worker_harness,
):
    coordinator = worker_harness["coordinator"]
    worker = worker_harness["principals"]["worker-old"]
    transport = worker_harness["transport"]
    transport.fail_next_send = True

    with pytest.raises(WorkerCoordinatorError) as error:
        coordinator.lease("task-1", worker, idempotency_key="task-1-effect")
    assert error.value.code == "WORKER_DISPATCH_OUTCOME_UNKNOWN"

    with pytest.raises(WorkerCoordinatorError) as second:
        coordinator.lease(
            "task-1",
            worker_harness["principals"]["worker-new"],
            idempotency_key="task-1-effect",
        )
    assert second.value.code == "TASK_NOT_READY"
    assert transport.messages == []
