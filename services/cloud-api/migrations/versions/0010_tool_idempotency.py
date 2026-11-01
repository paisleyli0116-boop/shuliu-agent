"""Persist side-effecting Tool idempotency claims and outcomes."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_tool_idempotency"
down_revision = "0009_knowledge_embedding_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_idempotency",
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("tool_id", sa.String(length=128), nullable=False),
        sa.Column("operation_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("request_digest", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('CLAIMED', 'DISPATCHED', 'COMPLETED', 'INDETERMINATE')",
            name="ck_tool_idempotency_status",
        ),
        sa.CheckConstraint(
            "length(request_digest) BETWEEN 1 AND 128",
            name="ck_tool_idempotency_digest_length",
        ),
        sa.CheckConstraint(
            "length(organization_id) BETWEEN 1 AND 128 AND "
            "length(tool_id) BETWEEN 1 AND 128 AND "
            "length(operation_id) BETWEEN 1 AND 128",
            name="ck_tool_idempotency_scope_length",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 256",
            name="ck_tool_idempotency_key_length",
        ),
        sa.CheckConstraint(
            "(status = 'COMPLETED' AND result_json IS NOT NULL) OR "
            "(status <> 'COMPLETED' AND result_json IS NULL)",
            name="ck_tool_idempotency_completed_result",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "tool_id",
            "operation_id",
            "idempotency_key",
            name="uq_tool_idempotency_scope_key",
        ),
    )
    op.create_index(
        "ix_tool_idempotency_status_updated",
        "tool_idempotency",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tool_idempotency_status_updated", table_name="tool_idempotency"
    )
    op.drop_table("tool_idempotency")
