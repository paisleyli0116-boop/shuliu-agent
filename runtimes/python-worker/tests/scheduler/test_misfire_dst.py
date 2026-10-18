from __future__ import annotations

from datetime import UTC, datetime

import pytest
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import create_engine

from shuliu_runtime.scheduler.occurrence import (
    CoalescePolicy,
    Schedule,
    ScheduleValidationError,
    create_occurrence,
)
from shuliu_runtime.scheduler.service import build_trigger
from shuliu_runtime.scheduler.store import SCHEDULER_METADATA, ScheduleStore

pytestmark = pytest.mark.integration


def _schedule(*, timezone="America/New_York", trigger=None):
    return Schedule(
        schedule_id="dst-schedule",
        organization_id="org-1",
        workflow_version_id="workflow-v1",
        trigger=trigger or {"type": "cron", "hour": 2, "minute": 30},
        timezone=timezone,
        coalesce=CoalescePolicy.LATEST,
        misfire_grace_time=120,
    )


def test_timezone_must_be_explicit_iana_zone_and_trigger_uses_that_zone():
    with pytest.raises(ScheduleValidationError, match="timezone"):
        _schedule(timezone="")
    with pytest.raises(ScheduleValidationError, match="timezone"):
        _schedule(timezone="Not/A_Real_Zone")

    trigger = build_trigger(_schedule())
    assert isinstance(trigger, CronTrigger)
    assert str(trigger.timezone) == "America/New_York"


def test_cron_trigger_uses_explicit_timezone_for_spring_forward_occurrence():
    trigger = build_trigger(_schedule())
    after = datetime(2026, 3, 8, 0, 0, tzinfo=trigger.timezone)

    next_fire = trigger.get_next_fire_time(None, after)

    assert next_fire is not None
    assert next_fire.tzinfo == trigger.timezone
    assert next_fire.astimezone(UTC) > after.astimezone(UTC)


def test_repeated_dst_wall_time_produces_distinct_utc_occurrence_ids():
    schedule = _schedule(trigger={"type": "cron", "hour": 1, "minute": 30})
    first_fold = datetime.fromisoformat("2026-11-01T01:30:00-04:00")
    second_fold = datetime.fromisoformat("2026-11-01T01:30:00-05:00")

    first = create_occurrence(schedule, first_fold)
    second = create_occurrence(schedule, second_fold)

    assert first.scheduled_at.astimezone(UTC) != second.scheduled_at.astimezone(UTC)
    assert first.occurrence_id != second.occurrence_id
    assert first.idempotency_key != second.idempotency_key


def test_naive_occurrence_time_is_rejected_instead_of_using_host_timezone():
    schedule = _schedule()
    with pytest.raises(ScheduleValidationError, match="timezone-aware"):
        create_occurrence(schedule, datetime(2026, 11, 1, 1, 30))
