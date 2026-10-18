"""Align the fencing-token uniqueness scope with per-task lease generations."""

from __future__ import annotations

from alembic import op


revision = "0007_task_scoped_fencing_tokens"
down_revision = "0006_worker_authentication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("worker_leases") as batch:
        batch.drop_constraint("uq_worker_fencing_token", type_="unique")
        batch.create_unique_constraint(
            "uq_worker_fencing_token",
            ["organization_id", "task_id", "fencing_token"],
        )


def downgrade() -> None:
    with op.batch_alter_table("worker_leases") as batch:
        batch.drop_constraint("uq_worker_fencing_token", type_="unique")
        batch.create_unique_constraint(
            "uq_worker_fencing_token",
            ["organization_id", "worker_id", "fencing_token"],
        )
