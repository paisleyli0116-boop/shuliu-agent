from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Iterable

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Float,
    Index,
    Integer,
    JSON,
    LargeBinary,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    and_,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine

from .occurrence import (
    CoalescePolicy,
    OccurrenceStatus,
    Schedule,
    ScheduleOccurrence,
    ScheduleValidationError,
    create_occurrence,
)


SCHEDULER_METADATA = MetaData()

schedules = Table(
    "schedules",
    SCHEDULER_METADATA,
    # Schedule IDs are stable platform identifiers and globally unique in V0.1.
    Column("schedule_id", String(128), primary_key=True),
    Column("organization_id", String(128), nullable=False),
    Column("workflow_version_id", String(128), nullable=False),
    Column("trigger_json", JSON, nullable=False),
    Column("timezone", String(128), nullable=False),
    Column("coalesce", String(16), nullable=False),
    Column("misfire_grace_time", Integer, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("organization_id", "schedule_id", name="uq_schedules_org_schedule"),
)

schedule_occurrences = Table(
    "schedule_occurrences",
    SCHEDULER_METADATA,
    Column("occurrence_id", String(128), primary_key=True),
    Column("organization_id", String(128), nullable=False),
    Column("schedule_id", String(128), nullable=False),
    Column("scheduled_at", DateTime(timezone=True), nullable=False),
    Column("idempotency_key", String(256), nullable=False),
    Column("status", String(32), nullable=False),
    Column("workflow_run_id", String(128)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["organization_id", "schedule_id"],
        ["schedules.organization_id", "schedules.schedule_id"],
        name="fk_schedule_occurrences_schedule",
    ),
    UniqueConstraint(
        "organization_id", "schedule_id", "scheduled_at", name="uq_schedule_occurrence_time"
    ),
    UniqueConstraint("idempotency_key", name="uq_schedule_occurrence_idempotency"),
    UniqueConstraint(
        "organization_id", "schedule_id", "occurrence_id", name="uq_schedule_occurrence_scope"
    ),
)

Index(
    "uq_schedule_occurrences_one_active",
    schedule_occurrences.c.schedule_id,
    unique=True,
    sqlite_where=schedule_occurrences.c.status.in_(["CLAIMED", "RUNNING"]),
    postgresql_where=schedule_occurrences.c.status.in_(["CLAIMED", "RUNNING"]),
)
Index(
    "ix_schedule_occurrences_pending",
    schedule_occurrences.c.schedule_id,
    schedule_occurrences.c.status,
    schedule_occurrences.c.scheduled_at,
)

# APScheduler 3.x SQLAlchemyJobStore's durable table is part of the migration contract.
apscheduler_jobs = Table(
    "apscheduler_jobs",
    SCHEDULER_METADATA,
    Column("id", String(191), primary_key=True),
    Column("next_run_time", Float),
    Column("job_state", LargeBinary, nullable=False),
)
Index("ix_apscheduler_jobs_next_run_time", apscheduler_jobs.c.next_run_time)


class ScheduleStore:
    """Durable schedule and occurrence state backed by the Cloud PostgreSQL engine."""

    def __init__(self, engine: Engine):
        self.engine = engine

    def save_schedule(self, schedule: Schedule) -> None:
        now = datetime.now(UTC)
        values = _schedule_values(schedule)
        values["updated_at"] = now
        with self.engine.begin() as connection:
            current = connection.execute(
                select(schedules.c.created_at, schedules.c.organization_id).where(
                    schedules.c.schedule_id == schedule.schedule_id
                )
            ).first()
            if current:
                if current.organization_id != schedule.organization_id:
                    raise ScheduleValidationError(
                        "schedule_id cannot be reassigned to another organization"
                    )
                values["created_at"] = current.created_at
                connection.execute(
                    update(schedules)
                    .where(schedules.c.schedule_id == schedule.schedule_id)
                    .values(**values)
                )
            else:
                values["created_at"] = now
                connection.execute(insert(schedules).values(**values))

    def get_schedule(self, schedule_id: str) -> Schedule | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(schedules).where(schedules.c.schedule_id == schedule_id)
            ).mappings().first()
        return _schedule_from_row(row) if row else None

    def enabled_schedules(self) -> list[Schedule]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(schedules).where(schedules.c.enabled.is_(True)).order_by(schedules.c.schedule_id)
            ).mappings().all()
        return [_schedule_from_row(row) for row in rows]

    def record_occurrences(
        self, schedule: Schedule, scheduled_times: Iterable[datetime]
    ) -> list[ScheduleOccurrence]:
        occurrences = [create_occurrence(schedule, moment) for moment in scheduled_times]
        occurrences.sort(key=lambda item: item.scheduled_at)
        if not occurrences:
            return []

        now = datetime.now(UTC)
        with self.engine.begin() as connection:
            if schedule.coalesce is CoalescePolicy.LATEST:
                connection.execute(
                    update(schedule_occurrences)
                    .where(
                        and_(
                            schedule_occurrences.c.schedule_id == schedule.schedule_id,
                            schedule_occurrences.c.status == OccurrenceStatus.PENDING.value,
                        )
                    )
                    .values(status=OccurrenceStatus.COALESCED.value, updated_at=now)
                )
            latest_index = len(occurrences) - 1
            for index, occurrence in enumerate(occurrences):
                status = OccurrenceStatus.PENDING
                if schedule.coalesce is CoalescePolicy.LATEST and index < latest_index:
                    status = OccurrenceStatus.COALESCED
                _insert_occurrence(
                    connection,
                    occurrence,
                    status=status,
                    now=now,
                )
        return occurrences

    def record_missed(
        self, schedule: Schedule, scheduled_at: datetime
    ) -> ScheduleOccurrence:
        occurrence = create_occurrence(schedule, scheduled_at)
        with self.engine.begin() as connection:
            _insert_occurrence(
                connection,
                occurrence,
                status=OccurrenceStatus.MISSED,
                now=datetime.now(UTC),
            )
        return ScheduleOccurrence(
            occurrence_id=occurrence.occurrence_id,
            organization_id=occurrence.organization_id,
            schedule_id=occurrence.schedule_id,
            scheduled_at=occurrence.scheduled_at,
            idempotency_key=occurrence.idempotency_key,
            status=OccurrenceStatus.MISSED,
        )

    def occurrences_for_schedule(self, schedule_id: str) -> list[ScheduleOccurrence]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(schedule_occurrences)
                .where(schedule_occurrences.c.schedule_id == schedule_id)
                .order_by(schedule_occurrences.c.scheduled_at, schedule_occurrences.c.occurrence_id)
            ).mappings().all()
        return [_occurrence_from_row(row) for row in rows]

    def active_occurrence(self, schedule_id: str) -> ScheduleOccurrence | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(schedule_occurrences)
                .where(
                    and_(
                        schedule_occurrences.c.schedule_id == schedule_id,
                        schedule_occurrences.c.status.in_(
                            [OccurrenceStatus.CLAIMED.value, OccurrenceStatus.RUNNING.value]
                        ),
                    )
                )
            ).mappings().first()
        return _occurrence_from_row(row) if row else None

    def claim_next(self, schedule_id: str) -> ScheduleOccurrence | None:
        now = datetime.now(UTC)
        with self.engine.begin() as connection:
            active = connection.execute(
                select(schedule_occurrences)
                .where(
                    and_(
                        schedule_occurrences.c.schedule_id == schedule_id,
                        schedule_occurrences.c.status.in_(
                            [OccurrenceStatus.CLAIMED.value, OccurrenceStatus.RUNNING.value]
                        ),
                    )
                )
                .with_for_update()
            ).mappings().first()
            if active:
                if active["status"] == OccurrenceStatus.CLAIMED.value:
                    return _occurrence_from_row(active)
                return None

            row = connection.execute(
                select(schedule_occurrences)
                .where(
                    and_(
                        schedule_occurrences.c.schedule_id == schedule_id,
                        schedule_occurrences.c.status == OccurrenceStatus.PENDING.value,
                    )
                )
                .order_by(schedule_occurrences.c.scheduled_at, schedule_occurrences.c.occurrence_id)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).mappings().first()
            if not row:
                return None
            connection.execute(
                update(schedule_occurrences)
                .where(
                    and_(
                        schedule_occurrences.c.occurrence_id == row["occurrence_id"],
                        schedule_occurrences.c.status == OccurrenceStatus.PENDING.value,
                    )
                )
                .values(status=OccurrenceStatus.CLAIMED.value, updated_at=now)
            )
            claimed = connection.execute(
                select(schedule_occurrences).where(
                    schedule_occurrences.c.occurrence_id == row["occurrence_id"]
                )
            ).mappings().one()
        return _occurrence_from_row(claimed)

    def mark_running(self, occurrence_id: str, workflow_run_id: str) -> None:
        with self.engine.begin() as connection:
            result = connection.execute(
                update(schedule_occurrences)
                .where(
                    and_(
                        schedule_occurrences.c.occurrence_id == occurrence_id,
                        schedule_occurrences.c.status == OccurrenceStatus.CLAIMED.value,
                    )
                )
                .values(
                    status=OccurrenceStatus.RUNNING.value,
                    workflow_run_id=workflow_run_id,
                    updated_at=datetime.now(UTC),
                )
            )
            if result.rowcount != 1:
                raise RuntimeError("only a claimed occurrence can become running")

    def finish(self, occurrence_id: str, workflow_run_id: str, *, succeeded: bool) -> None:
        status = OccurrenceStatus.COMPLETED if succeeded else OccurrenceStatus.FAILED
        with self.engine.begin() as connection:
            result = connection.execute(
                update(schedule_occurrences)
                .where(
                    and_(
                        schedule_occurrences.c.occurrence_id == occurrence_id,
                        schedule_occurrences.c.workflow_run_id == workflow_run_id,
                        schedule_occurrences.c.status == OccurrenceStatus.RUNNING.value,
                    )
                )
                .values(status=status.value, updated_at=datetime.now(UTC))
            )
            if result.rowcount != 1:
                raise RuntimeError("workflow completion does not match a running occurrence")


def _schedule_values(schedule: Schedule) -> dict[str, object]:
    return {
        "schedule_id": schedule.schedule_id,
        "organization_id": schedule.organization_id,
        "workflow_version_id": schedule.workflow_version_id,
        "trigger_json": dict(schedule.trigger),
        "timezone": schedule.timezone,
        "coalesce": schedule.coalesce.value,
        "misfire_grace_time": schedule.misfire_grace_time,
        "enabled": schedule.enabled,
    }


def _schedule_from_row(row) -> Schedule:
    trigger = row["trigger_json"]
    if isinstance(trigger, str):
        trigger = json.loads(trigger)
    return Schedule(
        schedule_id=row["schedule_id"],
        organization_id=row["organization_id"],
        workflow_version_id=row["workflow_version_id"],
        trigger=trigger,
        timezone=row["timezone"],
        coalesce=CoalescePolicy(row["coalesce"]),
        misfire_grace_time=row["misfire_grace_time"],
        enabled=row["enabled"],
    )


def _insert_occurrence(connection, occurrence, *, status, now) -> None:
    values = {
        "occurrence_id": occurrence.occurrence_id,
        "organization_id": occurrence.organization_id,
        "schedule_id": occurrence.schedule_id,
        "scheduled_at": occurrence.scheduled_at,
        "idempotency_key": occurrence.idempotency_key,
        "status": status.value,
        "workflow_run_id": None,
        "created_at": now,
        "updated_at": now,
    }
    if connection.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

        statement = dialect_insert(schedule_occurrences).values(**values).on_conflict_do_nothing()
    elif connection.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert

        statement = dialect_insert(schedule_occurrences).values(**values).on_conflict_do_nothing()
    else:
        statement = insert(schedule_occurrences).values(**values)
    connection.execute(statement)


def _occurrence_from_row(row) -> ScheduleOccurrence:
    scheduled_at = row["scheduled_at"]
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=UTC)
    return ScheduleOccurrence(
        occurrence_id=row["occurrence_id"],
        organization_id=row["organization_id"],
        schedule_id=row["schedule_id"],
        scheduled_at=scheduled_at.astimezone(UTC),
        idempotency_key=row["idempotency_key"],
        status=OccurrenceStatus(row["status"]),
        workflow_run_id=row["workflow_run_id"],
    )
