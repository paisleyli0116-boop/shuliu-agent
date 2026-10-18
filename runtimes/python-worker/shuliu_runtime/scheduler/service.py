from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Protocol, Sequence
from zoneinfo import ZoneInfo

from apscheduler.events import (
    EVENT_JOB_MISSED,
    EVENT_JOB_SUBMITTED,
    JobExecutionEvent,
    JobSubmissionEvent,
)
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .occurrence import Schedule, ScheduleOccurrence
from .store import ScheduleStore


logger = logging.getLogger(__name__)


class WorkflowCommandPort(Protocol):
    """Queues a Workflow command; it must deduplicate retries by idempotency key."""

    def enqueue_workflow(
        self,
        schedule: Schedule,
        occurrence: ScheduleOccurrence,
        *,
        idempotency_key: str,
    ) -> str: ...


def _scheduler_job(schedule_id: str, *, definition_fingerprint: str) -> None:
    """APScheduler entrypoint. Trigger execution only; no business work occurs here."""
    del schedule_id, definition_fingerprint


def build_trigger(schedule: Schedule):
    timezone = ZoneInfo(schedule.timezone)
    fields = dict(schedule.trigger)
    trigger_type = fields.pop("type")
    if trigger_type == "interval":
        return IntervalTrigger(timezone=timezone, **fields)
    if trigger_type == "cron":
        return CronTrigger(timezone=timezone, **fields)
    if trigger_type == "date":
        run_date = fields.get("run_date")
        if isinstance(run_date, str):
            run_date = datetime.fromisoformat(run_date)
        if not isinstance(run_date, datetime) or run_date.tzinfo is None:
            raise ValueError("date trigger run_date must be an ISO timezone-aware datetime")
        return DateTrigger(run_date=run_date, timezone=timezone)
    raise ValueError("unsupported schedule trigger")


class ScheduleService:
    """Persists trigger occurrences and forwards them to the Workflow command port."""

    def __init__(
        self,
        *,
        store: ScheduleStore,
        workflow_queue: WorkflowCommandPort,
        scheduler: BackgroundScheduler | None = None,
    ):
        self.store = store
        self.workflow_queue = workflow_queue
        self.scheduler = scheduler or BackgroundScheduler(
            jobstores={"default": SQLAlchemyJobStore(engine=store.engine)},
            timezone="UTC",
        )
        self._listener_registered = False

    def register_schedule(self, schedule: Schedule) -> None:
        build_trigger(schedule)
        self.store.save_schedule(schedule)
        if self.scheduler.running:
            self._sync_schedule(schedule)

    def start(self) -> None:
        if self.scheduler.running:
            return
        schedules = self.store.enabled_schedules()
        self.scheduler.start(paused=True)
        if not self._listener_registered:
            self.scheduler.add_listener(
                self._on_scheduler_event, EVENT_JOB_SUBMITTED | EVENT_JOB_MISSED
            )
            self._listener_registered = True
        for schedule in schedules:
            self._sync_schedule(schedule)
        configured_ids = {schedule.schedule_id for schedule in schedules}
        for job in self.scheduler.get_jobs():
            if job.id not in configured_ids:
                self.scheduler.remove_job(job.id)
        self.scheduler.resume()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)

    def accept_submitted(
        self, schedule_id: str, scheduled_run_times: Sequence[datetime]
    ) -> None:
        schedule = self._enabled_schedule(schedule_id)
        self.store.record_occurrences(schedule, scheduled_run_times)
        self.dispatch_pending(schedule_id)

    def accept_missed(self, schedule_id: str, scheduled_at: datetime) -> ScheduleOccurrence:
        schedule = self._enabled_schedule(schedule_id)
        return self.store.record_missed(schedule, scheduled_at)

    def dispatch_pending(self, schedule_id: str) -> str | None:
        schedule = self._enabled_schedule(schedule_id)
        occurrence = self.store.claim_next(schedule_id)
        if occurrence is None:
            return None
        try:
            workflow_run_id = self.workflow_queue.enqueue_workflow(
                schedule,
                occurrence,
                idempotency_key=occurrence.idempotency_key,
            )
            if not isinstance(workflow_run_id, str) or not workflow_run_id.strip():
                raise ValueError("Workflow command port returned an invalid run ID")
            self.store.mark_running(occurrence.occurrence_id, workflow_run_id)
            return workflow_run_id
        except Exception:
            # Keep CLAIMED: a timeout can mean the queue accepted the command but its
            # acknowledgement was lost. Any retry uses this same durable occurrence key.
            logger.error(
                "Workflow enqueue outcome is uncertain; occurrence remains claimed",
                extra={"schedule_id": schedule_id, "occurrence_id": occurrence.occurrence_id},
            )
            return None

    def workflow_run_finished(
        self,
        schedule_id: str,
        occurrence_id: str,
        workflow_run_id: str,
        *,
        succeeded: bool,
    ) -> str | None:
        self.store.finish(occurrence_id, workflow_run_id, succeeded=succeeded)
        return self.dispatch_pending(schedule_id)

    def _enabled_schedule(self, schedule_id: str) -> Schedule:
        schedule = self.store.get_schedule(schedule_id)
        if schedule is None or not schedule.enabled:
            raise KeyError(f"enabled schedule {schedule_id!r} was not found")
        build_trigger(schedule)
        return schedule

    def _sync_schedule(self, schedule: Schedule) -> None:
        trigger = build_trigger(schedule) if schedule.enabled else None
        current = self.scheduler.get_job(schedule.schedule_id)
        if not schedule.enabled:
            if current:
                self.scheduler.remove_job(schedule.schedule_id)
            return
        fingerprint = _fingerprint(schedule)
        if current and current.kwargs.get("definition_fingerprint") == fingerprint:
            return
        self.scheduler.add_job(
            _scheduler_job,
            trigger=trigger,
            id=schedule.schedule_id,
            name=schedule.schedule_id,
            args=[schedule.schedule_id],
            kwargs={"definition_fingerprint": fingerprint},
            replace_existing=True,
            coalesce=schedule.coalesce.value == "latest",
            max_instances=1,
            misfire_grace_time=schedule.misfire_grace_time,
        )

    def _on_scheduler_event(self, event: JobSubmissionEvent | JobExecutionEvent) -> None:
        if event.code == EVENT_JOB_SUBMITTED:
            assert isinstance(event, JobSubmissionEvent)
            self.accept_submitted(event.job_id, event.scheduled_run_times)
        elif event.code == EVENT_JOB_MISSED:
            assert isinstance(event, JobExecutionEvent)
            self.accept_missed(event.job_id, event.scheduled_run_time)


def _fingerprint(schedule: Schedule) -> str:
    payload = {
        "schedule_id": schedule.schedule_id,
        "organization_id": schedule.organization_id,
        "workflow_version_id": schedule.workflow_version_id,
        "trigger": dict(schedule.trigger),
        "timezone": schedule.timezone,
        "coalesce": schedule.coalesce.value,
        "misfire_grace_time": schedule.misfire_grace_time,
        "enabled": schedule.enabled,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
