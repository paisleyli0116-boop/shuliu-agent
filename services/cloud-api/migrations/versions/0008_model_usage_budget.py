"""Persist model calls and organization-scoped budget reservations."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_model_usage_budget"
down_revision = "0007_task_scoped_fencing_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_calls",
        sa.Column("call_id", sa.String(length=128), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("requested_capability", sa.String(length=64), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Numeric(14, 3), nullable=False),
        sa.Column("cost", sa.Numeric(20, 8)),
        sa.Column("currency", sa.String(length=3), server_default="USD", nullable=False),
        sa.Column("error_code", sa.String(length=96)),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("latency_ms >= 0", name="ck_model_calls_latency_nonnegative"),
        sa.CheckConstraint("cost IS NULL OR cost >= 0", name="ck_model_calls_cost_nonnegative"),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED', 'FAILED', 'UNKNOWN')",
            name="ck_model_calls_status",
        ),
        sa.PrimaryKeyConstraint("call_id", "organization_id", name="pk_model_calls"),
    )
    op.create_table(
        "model_budget_accounts",
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("budget_ref", sa.String(length=256), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("limit_amount", sa.Numeric(20, 8), nullable=False),
        sa.Column("reservation_amount", sa.Numeric(20, 8), nullable=False),
        sa.Column("reserved_amount", sa.Numeric(20, 8), server_default="0", nullable=False),
        sa.Column("spent_amount", sa.Numeric(20, 8), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("limit_amount >= 0", name="ck_model_budget_limit_nonnegative"),
        sa.CheckConstraint("reservation_amount > 0", name="ck_model_budget_reservation_positive"),
        sa.CheckConstraint("reserved_amount >= 0", name="ck_model_budget_reserved_nonnegative"),
        sa.CheckConstraint("spent_amount >= 0", name="ck_model_budget_spent_nonnegative"),
        sa.PrimaryKeyConstraint("organization_id", "budget_ref", name="pk_model_budget_accounts"),
    )
    op.create_table(
        "model_budget_reservations",
        sa.Column("call_id", sa.String(length=128), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("budget_ref", sa.String(length=256), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("reserved_amount", sa.Numeric(20, 8), nullable=False),
        sa.Column("settled_amount", sa.Numeric(20, 8)),
        sa.Column("usage", sa.JSON()),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("reserved_amount > 0", name="ck_model_budget_reservation_amount_positive"),
        sa.CheckConstraint("settled_amount IS NULL OR settled_amount >= 0", name="ck_model_budget_settled_nonnegative"),
        sa.CheckConstraint(
            "status IN ('RESERVED', 'RECONCILIATION_REQUIRED', 'SETTLED', 'RELEASED')",
            name="ck_model_budget_reservation_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "budget_ref"],
            ["model_budget_accounts.organization_id", "model_budget_accounts.budget_ref"],
            name="fk_model_budget_reservation_account",
        ),
        sa.PrimaryKeyConstraint("call_id", "organization_id", name="pk_model_budget_reservations"),
    )
    op.create_index(
        "ix_model_calls_org_trace_created",
        "model_calls",
        ["organization_id", "trace_id", "created_at"],
    )
    op.create_index(
        "ix_model_budget_reservations_org_status",
        "model_budget_reservations",
        ["organization_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_budget_reservations_org_status", table_name="model_budget_reservations")
    op.drop_index("ix_model_calls_org_trace_created", table_name="model_calls")
    op.drop_table("model_budget_reservations")
    op.drop_table("model_budget_accounts")
    op.drop_table("model_calls")
