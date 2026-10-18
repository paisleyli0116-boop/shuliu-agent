from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.persistence.postgres import metadata, tasks, workers
from app.workers.coordinator import WorkerCoordinator
from app.workers.leases import LeaseConfig, WorkerPrincipal
from sqlalchemy import create_engine, insert


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, Any]]] = []
        self.fail_next_send = False

    def send(self, worker_id: str, message: dict[str, Any]) -> None:
        if self.fail_next_send:
            self.fail_next_send = False
            raise OSError("simulated connection outcome unknown")
        self.messages.append((worker_id, message))


@pytest.fixture
def worker_harness():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    clock = MutableClock(datetime(2026, 10, 18, tzinfo=UTC))
    transport = FakeWebSocket()
    principals = {
        "worker-old": WorkerPrincipal(worker_id="worker-old", organization_id="org-1"),
        "worker-new": WorkerPrincipal(worker_id="worker-new", organization_id="org-1"),
    }
    with engine.begin() as connection:
        for worker_id in principals:
            connection.execute(
                insert(workers).values(
                    worker_id=worker_id,
                    organization_id="org-1",
                    status="ONLINE",
                    created_at=clock(),
                    worker_version="0.1.0",
                    protocol_version=1,
                    capabilities=["worker.runtime", "tool.execute"],
                    last_heartbeat_at=clock(),
                )
            )
        connection.execute(
            insert(tasks).values(
                task_id="task-1",
                organization_id="org-1",
                status="READY",
                payload={"required_capabilities": ["worker.runtime"]},
                created_at=clock(),
            )
        )

    config = LeaseConfig(
        lease_duration=timedelta(seconds=30),
        heartbeat_interval=timedelta(seconds=5),
        grace_period=timedelta(seconds=10),
        max_attempts=3,
    )
    coordinator = WorkerCoordinator(
        engine=engine, transport=transport, config=config, clock=clock
    )
    try:
        yield {
            "engine": engine,
            "clock": clock,
            "transport": transport,
            "principals": principals,
            "coordinator": coordinator,
        }
    finally:
        engine.dispose()
