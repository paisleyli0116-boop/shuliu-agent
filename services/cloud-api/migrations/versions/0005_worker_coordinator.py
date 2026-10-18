"""Persist Worker capabilities, heartbeat health and lease idempotency."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_worker_coordinator"
down_revision = "0004_knowledge_pgvector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workers",
        sa.Column(
            "worker_version",
            sa.String(length=64),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "workers",
        sa.Column("protocol_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "workers",
        sa.Column("capabilities", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "workers",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "worker_leases",
        sa.Column("idempotency_key", sa.String(length=256), nullable=True),
    )
    op.create_index(
        "ix_worker_leases_org_task_status",
        "worker_leases",
        ["organization_id", "task_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_worker_leases_org_task_status", table_name="worker_leases")
    op.drop_column("worker_leases", "idempotency_key")
    op.drop_column("workers", "last_heartbeat_at")
    op.drop_column("workers", "capabilities")
    op.drop_column("workers", "protocol_version")
    op.drop_column("workers", "worker_version")
