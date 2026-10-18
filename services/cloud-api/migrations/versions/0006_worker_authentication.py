"""Add one-time Worker enrollment and replay-protected session credentials."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_worker_authentication"
down_revision = "0005_worker_coordinator"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workers", sa.Column("device_key_algorithm", sa.String(length=32)))
    op.add_column("workers", sa.Column("device_public_key", sa.LargeBinary()))
    op.create_table(
        "worker_registration_tokens",
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("token_digest"),
    )
    op.create_index(
        "ix_worker_registration_tokens_org_expiry",
        "worker_registration_tokens",
        ["organization_id", "expires_at"],
    )
    op.create_table(
        "worker_session_tokens",
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "worker_id"],
            ["workers.organization_id", "workers.worker_id"],
            name="fk_worker_session_tokens_worker",
        ),
        sa.PrimaryKeyConstraint("jti"),
    )
    op.create_index(
        "ix_worker_session_tokens_expiry", "worker_session_tokens", ["expires_at"]
    )
    op.create_table(
        "worker_auth_challenges",
        sa.Column("challenge_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "worker_id"],
            ["workers.organization_id", "workers.worker_id"],
            name="fk_worker_auth_challenges_worker",
        ),
        sa.PrimaryKeyConstraint("challenge_id"),
    )
    op.create_index(
        "ix_worker_auth_challenges_expiry", "worker_auth_challenges", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_worker_auth_challenges_expiry", table_name="worker_auth_challenges")
    op.drop_table("worker_auth_challenges")
    op.drop_index("ix_worker_session_tokens_expiry", table_name="worker_session_tokens")
    op.drop_table("worker_session_tokens")
    op.drop_index(
        "ix_worker_registration_tokens_org_expiry",
        table_name="worker_registration_tokens",
    )
    op.drop_table("worker_registration_tokens")
    op.drop_column("workers", "device_public_key")
    op.drop_column("workers", "device_key_algorithm")
