"""Record explicit profile metadata for Knowledge pgvector rows.

Revision ID: 0009_knowledge_embedding_profile
Revises: 0008_model_usage_budget
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_knowledge_embedding_profile"
down_revision = "0008_model_usage_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable columns preserve existing vectors as legacy/unprofiled rows.
    # They will not participate in profile-scoped retrieval until re-ingested.
    op.add_column("knowledge_chunks", sa.Column("embedding_profile_id", sa.String(length=128)))
    op.add_column("knowledge_chunks", sa.Column("embedding_provider_id", sa.String(length=128)))
    op.add_column("knowledge_chunks", sa.Column("embedding_model", sa.String(length=256)))
    op.add_column("knowledge_chunks", sa.Column("embedding_dimensions", sa.Integer()))
    op.add_column("knowledge_chunks", sa.Column("embedding_index_version", sa.Integer()))
    check_sql = (
        "(embedding_profile_id IS NULL AND embedding_provider_id IS NULL "
        "AND embedding_model IS NULL AND embedding_dimensions IS NULL "
        "AND embedding_index_version IS NULL) OR "
        "(embedding_profile_id IS NOT NULL AND embedding_provider_id IS NOT NULL "
        "AND embedding_model IS NOT NULL AND embedding_dimensions IS NOT NULL "
        "AND embedding_dimensions > 0 AND embedding_index_version IS NOT NULL "
        "AND embedding_index_version > 0)"
    )
    if op.get_context().dialect.name == "sqlite":
        # The repository's migration contract suite exercises SQLite too; batch
        # mode is limited to that test dialect because PostgreSQL supports ADD CONSTRAINT.
        with op.batch_alter_table("knowledge_chunks") as batch:
            batch.create_check_constraint(
                "ck_knowledge_chunks_embedding_profile_complete", check_sql
            )
    else:
        op.create_check_constraint(
            "ck_knowledge_chunks_embedding_profile_complete",
            "knowledge_chunks",
            check_sql,
        )
    op.create_index(
        "ix_knowledge_chunks_embedding_profile",
        "knowledge_chunks",
        ["organization_id", "embedding_profile_id", "embedding_index_version"],
    )
    op.create_table(
        "knowledge_embedding_profiles",
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("index_version", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("dimensions > 0", name="ck_knowledge_embedding_profiles_dimensions_positive"),
        sa.CheckConstraint("index_version > 0", name="ck_knowledge_embedding_profiles_version_positive"),
        sa.CheckConstraint("state IN ('BUILDING', 'READY')", name="ck_knowledge_embedding_profiles_state"),
        sa.PrimaryKeyConstraint(
            "organization_id", "profile_id", "index_version",
            name="pk_knowledge_embedding_profiles",
        ),
    )
    op.create_index(
        "ix_knowledge_embedding_profiles_state",
        "knowledge_embedding_profiles",
        ["organization_id", "state"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_embedding_profiles_state", table_name="knowledge_embedding_profiles")
    op.drop_table("knowledge_embedding_profiles")
    op.drop_index("ix_knowledge_chunks_embedding_profile", table_name="knowledge_chunks")
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("knowledge_chunks") as batch:
            batch.drop_constraint(
                "ck_knowledge_chunks_embedding_profile_complete", type_="check"
            )
            batch.drop_column("embedding_index_version")
            batch.drop_column("embedding_dimensions")
            batch.drop_column("embedding_model")
            batch.drop_column("embedding_provider_id")
            batch.drop_column("embedding_profile_id")
    else:
        op.drop_constraint(
            "ck_knowledge_chunks_embedding_profile_complete", "knowledge_chunks", type_="check"
        )
        op.drop_column("knowledge_chunks", "embedding_index_version")
        op.drop_column("knowledge_chunks", "embedding_dimensions")
        op.drop_column("knowledge_chunks", "embedding_model")
        op.drop_column("knowledge_chunks", "embedding_provider_id")
        op.drop_column("knowledge_chunks", "embedding_profile_id")
