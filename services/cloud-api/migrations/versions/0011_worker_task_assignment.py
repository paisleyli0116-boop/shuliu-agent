"""Define the explicit permission required to assign Cloud tasks to Workers."""

from __future__ import annotations

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision = "0011_worker_task_assignment"
down_revision = "0010_tool_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    permissions = sa.table(
        "permissions",
        sa.column("permission_id", sa.String),
        sa.column("permission_code", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        permissions,
        [
            {
                "permission_id": "foundation.task.assign_worker",
                "permission_code": "foundation.task.assign_worker",
                "created_at": datetime.now(UTC),
            }
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM permissions "
            "WHERE permission_id = :permission_id "
            "AND permission_code = :permission_code"
        ).bindparams(
            permission_id="foundation.task.assign_worker",
            permission_code="foundation.task.assign_worker",
        )
    )
