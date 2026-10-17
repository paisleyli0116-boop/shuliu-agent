"""Create organization-scoped Foundation persistence tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_foundation_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "schema_migrations",
        sa.Column("migration_id", sa.String(length=128), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("migration_id"),
    )
    op.create_table(
        "tasks",
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("task_id"),
        sa.UniqueConstraint("organization_id", "task_id", name="uq_tasks_org_task"),
    )
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "task_id"],
            ["tasks.organization_id", "tasks.task_id"],
            name="fk_agent_runs_org_task",
        ),
        sa.PrimaryKeyConstraint("run_id"),
        sa.UniqueConstraint("organization_id", "run_id", name="uq_agent_runs_org_run"),
    )
    op.create_table(
        "events",
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_events_org_idempotency"
        ),
    )
    op.create_index(
        "ix_events_org_received_at", "events", ["organization_id", "received_at"]
    )
    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.String(length=128), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("action_digest", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["agent_runs.organization_id", "agent_runs.run_id"],
            name="fk_approvals_org_run",
        ),
        sa.PrimaryKeyConstraint("approval_id"),
        sa.UniqueConstraint(
            "organization_id", "approval_id", name="uq_approvals_org_id"
        ),
    )
    op.create_table(
        "workers",
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("worker_id"),
        sa.UniqueConstraint(
            "organization_id", "worker_id", name="uq_workers_org_worker"
        ),
    )
    op.create_table(
        "worker_leases",
        sa.Column("lease_id", sa.String(length=128), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "worker_id"],
            ["workers.organization_id", "workers.worker_id"],
            name="fk_worker_leases_org_worker",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "task_id"],
            ["tasks.organization_id", "tasks.task_id"],
            name="fk_worker_leases_org_task",
        ),
        sa.PrimaryKeyConstraint("lease_id"),
        sa.UniqueConstraint(
            "organization_id",
            "worker_id",
            "fencing_token",
            name="uq_worker_fencing_token",
        ),
    )
    op.create_index(
        "ix_worker_leases_org_status",
        "worker_leases",
        ["organization_id", "status"],
    )
    op.create_table(
        "knowledge_documents",
        sa.Column("document_id", sa.String(length=128), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("scope", sa.String(length=128), nullable=False),
        sa.Column("classification", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("document_id"),
        sa.UniqueConstraint(
            "organization_id",
            "document_id",
            name="uq_knowledge_documents_org_document",
        ),
    )
    op.create_index(
        "ix_knowledge_documents_acl",
        "knowledge_documents",
        ["organization_id", "scope", "classification"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_documents_acl", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
    op.drop_index("ix_worker_leases_org_status", table_name="worker_leases")
    op.drop_table("worker_leases")
    op.drop_table("workers")
    op.drop_table("approvals")
    op.drop_index("ix_events_org_received_at", table_name="events")
    op.drop_table("events")
    op.drop_table("agent_runs")
    op.drop_table("tasks")
    op.drop_table("schema_migrations")
