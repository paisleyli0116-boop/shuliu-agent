"""Add durable schedule definitions, occurrences, and APScheduler job storage."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_scheduler"
down_revision = "0002_identity_rbac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("schedule_id", sa.String(length=128), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("workflow_version_id", sa.String(length=128), nullable=False),
        sa.Column("trigger_json", sa.JSON(), nullable=False),
        sa.Column("timezone", sa.String(length=128), nullable=False),
        sa.Column("coalesce", sa.String(length=16), nullable=False),
        sa.Column("misfire_grace_time", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("schedule_id"),
        sa.UniqueConstraint("organization_id", "schedule_id", name="uq_schedules_org_schedule"),
    )
    op.create_table(
        "schedule_occurrences",
        sa.Column("occurrence_id", sa.String(length=128), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("schedule_id", sa.String(length=128), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "schedule_id"],
            ["schedules.organization_id", "schedules.schedule_id"],
            name="fk_schedule_occurrences_schedule",
        ),
        sa.PrimaryKeyConstraint("occurrence_id"),
        sa.UniqueConstraint(
            "organization_id",
            "schedule_id",
            "scheduled_at",
            name="uq_schedule_occurrence_time",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_schedule_occurrence_idempotency"),
        sa.UniqueConstraint(
            "organization_id",
            "schedule_id",
            "occurrence_id",
            name="uq_schedule_occurrence_scope",
        ),
    )
    op.create_index(
        "uq_schedule_occurrences_one_active",
        "schedule_occurrences",
        ["schedule_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('CLAIMED', 'RUNNING')"),
        postgresql_where=sa.text("status IN ('CLAIMED', 'RUNNING')"),
    )
    op.create_index(
        "ix_schedule_occurrences_pending",
        "schedule_occurrences",
        ["schedule_id", "status", "scheduled_at"],
    )
    op.create_table(
        "apscheduler_jobs",
        sa.Column("id", sa.Unicode(length=191), nullable=False),
        sa.Column("next_run_time", sa.Float(), nullable=True),
        sa.Column("job_state", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_apscheduler_jobs_next_run_time", "apscheduler_jobs", ["next_run_time"]
    )


def downgrade() -> None:
    op.drop_index("ix_apscheduler_jobs_next_run_time", table_name="apscheduler_jobs")
    op.drop_table("apscheduler_jobs")
    op.drop_index("ix_schedule_occurrences_pending", table_name="schedule_occurrences")
    op.drop_index("uq_schedule_occurrences_one_active", table_name="schedule_occurrences")
    op.drop_table("schedule_occurrences")
    op.drop_table("schedules")
