from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import create_engine
from sqlalchemy.schema import MetaData

from shuliu_runtime.scheduler.occurrence import (
    CoalescePolicy,
    OccurrenceStatus,
    Schedule,
)
from shuliu_runtime.scheduler.service import ScheduleService
from shuliu_runtime.scheduler.store import SCHEDULER_METADATA, ScheduleStore

pytestmark = pytest.mark.integration


def _schedule(
    *, schedule_id="schedule-1", coalesce=CoalescePolicy.LATEST, seconds=3600
):
    return Schedule(
        schedule_id=schedule_id,
        organization_id="org-1",
        workflow_version_id="workflow-v1",
        trigger={"type": "interval", "seconds": seconds},
        timezone="UTC",
        coalesce=coalesce,
        misfire_grace_time=60,
    )


class _WorkflowQueue:
    def __init__(self, *, fail_once=False):
        self.calls = []
        self.fail_once = fail_once

    def enqueue_workflow(self, schedule, occurrence, *, idempotency_key):
        self.calls.append((schedule, occurrence, idempotency_key))
        if self.fail_once:
            self.fail_once = False
            raise TimeoutError("workflow queue acknowledgement was lost")
        return f"run:{occurrence.occurrence_id}"


def _service(tmp_path, *, queue=None):
    engine = create_engine(f"sqlite:///{tmp_path / 'scheduler.sqlite3'}")
    SCHEDULER_METADATA.create_all(engine)
    store = ScheduleStore(engine)
    job_store = SQLAlchemyJobStore(
        engine=engine,
        tablename="apscheduler_jobs",
        metadata=MetaData(),
    )
    scheduler = BackgroundScheduler(
        jobstores={"default": job_store}, timezone=UTC
    )
    return engine, ScheduleService(
        store=store,
        workflow_queue=queue or _WorkflowQueue(),
        scheduler=scheduler,
    )


def test_persistent_schedule_restarts_and_same_id_registration_replaces_config(tmp_path):
    engine, first = _service(tmp_path)
    first.register_schedule(_schedule())
    first.start()
    first.shutdown()

    queue = _WorkflowQueue()
    second_engine = create_engine(f"sqlite:///{tmp_path / 'scheduler.sqlite3'}")
    second = ScheduleService(
        store=ScheduleStore(second_engine),
        workflow_queue=queue,
        scheduler=BackgroundScheduler(
            jobstores={
                "default": SQLAlchemyJobStore(
                    engine=second_engine,
                    tablename="apscheduler_jobs",
                    metadata=MetaData(),
                )
            },
            timezone=UTC,
        ),
    )
    second.register_schedule(
        _schedule(coalesce=CoalescePolicy.ALL, seconds=7200)
    )
    second.start()

    jobs = second.scheduler.get_jobs()
    assert len(jobs) == 1
    job = second.scheduler.get_job("schedule-1")
    assert job is not None
    assert job.coalesce is False
    assert job.max_instances == 1
    assert job.misfire_grace_time == 60
    assert job.trigger.interval.total_seconds() == 7200
    assert second.store.get_schedule("schedule-1").coalesce is CoalescePolicy.ALL

    second.shutdown()
    engine.dispose()
    second_engine.dispose()


def test_schedule_id_cannot_be_reassigned_to_another_organization(tmp_path):
    engine, service = _service(tmp_path)
    service.register_schedule(_schedule())
    reassigned = Schedule(
        schedule_id="schedule-1",
        organization_id="org-2",
        workflow_version_id="workflow-v2",
        trigger={"type": "interval", "seconds": 3600},
        timezone="UTC",
        coalesce=CoalescePolicy.LATEST,
        misfire_grace_time=60,
    )

    with pytest.raises(ValueError, match="organization"):
        service.register_schedule(reassigned)

    assert service.store.get_schedule("schedule-1").organization_id == "org-1"
    engine.dispose()


def test_invalid_persisted_trigger_fails_closed_before_workflow_queue(tmp_path):
    engine, service = _service(tmp_path)
    invalid_schedule = _schedule()
    service.store.save_schedule(
        Schedule(
            schedule_id=invalid_schedule.schedule_id,
            organization_id=invalid_schedule.organization_id,
            workflow_version_id=invalid_schedule.workflow_version_id,
            trigger={"type": "cron", "hour": 99, "minute": 0},
            timezone="UTC",
            coalesce=CoalescePolicy.LATEST,
            misfire_grace_time=60,
        )
    )

    with pytest.raises(ValueError):
        service.accept_submitted(
            invalid_schedule.schedule_id,
            [datetime(2026, 10, 18, 0, 0, tzinfo=UTC)],
        )

    assert service.workflow_queue.calls == []
    assert service.store.occurrences_for_schedule(invalid_schedule.schedule_id) == []
    engine.dispose()


@pytest.mark.parametrize(
    ("coalesce", "expected_pending", "expected_coalesced", "expected_running"),
    [
        (CoalescePolicy.LATEST, 0, 2, 1),
        (CoalescePolicy.ALL, 2, 0, 1),
    ],
)
def test_missed_occurrences_follow_latest_or_explicit_all_policy(
    tmp_path, coalesce, expected_pending, expected_coalesced, expected_running
):
    engine, service = _service(tmp_path)
    schedule = _schedule(coalesce=coalesce)
    service.register_schedule(schedule)
    start = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)

    service.accept_submitted(
        schedule.schedule_id,
        [start, start + timedelta(minutes=1), start + timedelta(minutes=2)],
    )

    occurrences = service.store.occurrences_for_schedule(schedule.schedule_id)
    assert sum(row.status is OccurrenceStatus.PENDING for row in occurrences) == expected_pending
    assert sum(row.status is OccurrenceStatus.COALESCED for row in occurrences) == expected_coalesced
    assert sum(row.status is OccurrenceStatus.RUNNING for row in occurrences) == expected_running
    assert len(service.workflow_queue.calls) == 1
    assert service.workflow_queue.calls[0][2] == service.workflow_queue.calls[0][1].idempotency_key
    engine.dispose()


def test_same_schedule_runs_are_serialized_and_next_occurrence_starts_after_completion(
    tmp_path,
):
    engine, service = _service(tmp_path)
    schedule = _schedule(coalesce=CoalescePolicy.ALL)
    service.register_schedule(schedule)
    first_time = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)
    second_time = first_time + timedelta(minutes=1)

    service.accept_submitted(schedule.schedule_id, [first_time])
    service.accept_submitted(schedule.schedule_id, [second_time])

    assert len(service.workflow_queue.calls) == 1
    first = service.store.active_occurrence(schedule.schedule_id)
    assert first.status is OccurrenceStatus.RUNNING
    assert first.scheduled_at == first_time

    service.workflow_run_finished(
        schedule.schedule_id,
        first.occurrence_id,
        first.workflow_run_id,
        succeeded=True,
    )

    assert len(service.workflow_queue.calls) == 2
    second = service.store.active_occurrence(schedule.schedule_id)
    assert second.status is OccurrenceStatus.RUNNING
    assert second.scheduled_at == second_time
    engine.dispose()


def test_uncertain_workflow_enqueue_retries_with_same_occurrence_idempotency_key(
    tmp_path,
):
    queue = _WorkflowQueue(fail_once=True)
    engine, service = _service(tmp_path, queue=queue)
    schedule = _schedule()
    service.register_schedule(schedule)
    scheduled_at = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)

    service.accept_submitted(schedule.schedule_id, [scheduled_at])
    claim_after_uncertain_ack = service.store.active_occurrence(schedule.schedule_id)
    assert claim_after_uncertain_ack.status is OccurrenceStatus.CLAIMED

    service.dispatch_pending(schedule.schedule_id)

    assert len(queue.calls) == 2
    assert queue.calls[0][2] == queue.calls[1][2]
    assert queue.calls[0][1].occurrence_id == queue.calls[1][1].occurrence_id
    assert service.store.active_occurrence(schedule.schedule_id).status is OccurrenceStatus.RUNNING
    engine.dispose()


def test_expired_misfire_is_recorded_and_never_enqueued(tmp_path):
    engine, service = _service(tmp_path)
    schedule = _schedule()
    service.register_schedule(schedule)
    scheduled_at = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)

    occurrence = service.accept_missed(schedule.schedule_id, scheduled_at)

    assert occurrence.status is OccurrenceStatus.MISSED
    assert service.workflow_queue.calls == []
    engine.dispose()
