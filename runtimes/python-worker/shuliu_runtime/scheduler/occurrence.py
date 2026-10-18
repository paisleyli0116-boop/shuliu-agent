from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ScheduleValidationError(ValueError):
    """A schedule is incomplete or has an unsupported trigger definition."""


class CoalescePolicy(str, Enum):
    LATEST = "latest"
    ALL = "all"


class OccurrenceStatus(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    MISSED = "MISSED"
    COALESCED = "COALESCED"


_TRIGGER_FIELDS = {
    "interval": {
        "weeks",
        "days",
        "hours",
        "minutes",
        "seconds",
        "start_date",
        "end_date",
        "jitter",
    },
    "cron": {
        "year",
        "month",
        "week",
        "day",
        "day_of_week",
        "hour",
        "minute",
        "second",
        "start_date",
        "end_date",
        "jitter",
    },
    "date": {"run_date"},
}


@dataclass(frozen=True)
class Schedule:
    schedule_id: str
    organization_id: str
    workflow_version_id: str
    trigger: Mapping[str, Any]
    timezone: str
    coalesce: CoalescePolicy = CoalescePolicy.LATEST
    misfire_grace_time: int = 60
    enabled: bool = True

    def __post_init__(self) -> None:
        for field_name in ("schedule_id", "organization_id", "workflow_version_id"):
            if not getattr(self, field_name).strip():
                raise ScheduleValidationError(f"{field_name} is required")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ScheduleValidationError("timezone must be a valid IANA timezone") from exc
        if (
            isinstance(self.misfire_grace_time, bool)
            or not isinstance(self.misfire_grace_time, int)
            or self.misfire_grace_time < 1
        ):
            raise ScheduleValidationError("misfire_grace_time must be a positive integer")
        try:
            policy = CoalescePolicy(self.coalesce)
        except ValueError as exc:
            raise ScheduleValidationError("coalesce must be latest or all") from exc
        object.__setattr__(self, "coalesce", policy)

        trigger = dict(self.trigger)
        trigger_type = trigger.pop("type", None)
        if trigger_type not in _TRIGGER_FIELDS:
            raise ScheduleValidationError("trigger type must be interval, cron, or date")
        unsupported = set(trigger) - _TRIGGER_FIELDS[trigger_type]
        if unsupported:
            raise ScheduleValidationError(
                f"unsupported {trigger_type} trigger fields: {sorted(unsupported)}"
            )
        if trigger_type == "interval" and not any(
            trigger.get(unit, 0)
            for unit in ("weeks", "days", "hours", "minutes", "seconds")
        ):
            raise ScheduleValidationError("interval trigger needs a positive interval")
        object.__setattr__(self, "trigger", {"type": trigger_type, **trigger})


@dataclass(frozen=True)
class ScheduleOccurrence:
    occurrence_id: str
    organization_id: str
    schedule_id: str
    scheduled_at: datetime
    idempotency_key: str
    status: OccurrenceStatus = OccurrenceStatus.PENDING
    workflow_run_id: str | None = None


def create_occurrence(schedule: Schedule, scheduled_at: datetime) -> ScheduleOccurrence:
    if scheduled_at.tzinfo is None or scheduled_at.utcoffset() is None:
        raise ScheduleValidationError("occurrence time must be timezone-aware")
    utc_time = scheduled_at.astimezone(UTC)
    stable_time = utc_time.isoformat(timespec="microseconds")
    occurrence_id = str(
        uuid5(NAMESPACE_URL, f"shuliu:schedule:{schedule.schedule_id}:{stable_time}")
    )
    return ScheduleOccurrence(
        occurrence_id=occurrence_id,
        organization_id=schedule.organization_id,
        schedule_id=schedule.schedule_id,
        scheduled_at=utc_time,
        idempotency_key=f"schedule:{schedule.schedule_id}:occurrence:{occurrence_id}",
    )
