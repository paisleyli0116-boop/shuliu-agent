from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.persistence.postgres import (
    create_postgres_engine,
    events,
    tasks,
    worker_leases,
    workers,
)
from app.workers.coordinator import WorkerCoordinator
from app.workers.dispatch import WorkerTransport
from app.workers.leases import (
    LeaseConfig,
    RecoveryDecision,
    WorkerCoordinatorError,
    WorkerPrincipal,
)
from sqlalchemy import delete, insert


class SilentTransport(WorkerTransport):
    def send(self, worker_id: str, message: dict) -> None:
        return None


@pytest.mark.integration
def test_postgres_serializes_task_claim_and_fencing_token_increments():
    database_url = os.environ.get("SHULIU_DATABASE_URL")
    if not database_url:
        pytest.skip("SHULIU_DATABASE_URL is not configured")

    engine = create_postgres_engine(database_url)
    suffix = uuid4().hex[:12]
    organization_id = f"org-worker-{suffix}"
    task_id = f"task-worker-{suffix}"
    worker_ids = (f"worker-a-{suffix}", f"worker-b-{suffix}")
    principals = tuple(
        WorkerPrincipal(worker_id=worker_id, organization_id=organization_id)
        for worker_id in worker_ids
    )
    now = [datetime.now(UTC)]
    config = LeaseConfig(
        lease_duration=timedelta(seconds=30),
        heartbeat_interval=timedelta(seconds=5),
        grace_period=timedelta(seconds=10),
        max_attempts=3,
    )
    coordinators = tuple(
        WorkerCoordinator(
            engine=engine,
            transport=SilentTransport(),
            config=config,
            clock=lambda: now[0],
        )
        for _ in principals
    )
    try:
        for coordinator, principal in zip(coordinators, principals):
            coordinator.record_authenticated_worker(
                principal,
                worker_version="0.1.0",
                protocol_version=1,
                capabilities=("worker.runtime",),
            )
        with engine.begin() as connection:
            connection.execute(
                insert(tasks).values(
                    task_id=task_id,
                    organization_id=organization_id,
                    status="READY",
                    payload={"required_capabilities": ["worker.runtime"]},
                    created_at=now[0],
                )
            )

        def claim(index: int):
            try:
                return coordinators[index].lease(
                    task_id,
                    principals[index],
                    idempotency_key=f"effect-{suffix}",
                )
            except WorkerCoordinatorError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(claim, (0, 1)))
        grants = [item for item in outcomes if not isinstance(item, str)]
        errors = [item for item in outcomes if isinstance(item, str)]
        assert len(grants) == 1
        assert errors == ["TASK_NOT_READY"]

        first = grants[0]
        now[0] += timedelta(seconds=41)
        coordinators[0].reconcile_expired(
            first.lease_id, decision=RecoveryDecision.RETRY_SAFE
        )
        coordinators[1].heartbeat(principals[1])
        second = coordinators[1].lease(
            task_id,
            principals[1],
            idempotency_key=f"effect-{suffix}",
        )
        assert second.fencing_token > first.fencing_token

        def submit_result():
            return coordinators[1].commit_result(
                principals[1],
                lease_id=second.lease_id,
                fencing_token=second.fencing_token,
                idempotency_key=f"effect-{suffix}",
                result={"status": "SUCCEEDED", "value": "done"},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            acknowledgements = list(pool.map(lambda _: submit_result(), range(2)))
        assert sorted(item.duplicate for item in acknowledgements) == [False, True]
        assert acknowledgements[0].event_id == acknowledgements[1].event_id
    finally:
        with engine.begin() as connection:
            connection.execute(
                delete(events).where(events.c.organization_id == organization_id)
            )
            connection.execute(
                delete(worker_leases).where(worker_leases.c.task_id == task_id)
            )
            connection.execute(delete(tasks).where(tasks.c.task_id == task_id))
            connection.execute(
                delete(workers).where(workers.c.worker_id.in_(worker_ids))
            )
        engine.dispose()
