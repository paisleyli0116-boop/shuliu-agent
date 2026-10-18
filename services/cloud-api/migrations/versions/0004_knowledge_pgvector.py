"""Add organization-scoped Knowledge chunks, embeddings, and permission ACL."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.persistence.postgres import VectorType


revision = "0004_knowledge_pgvector"
down_revision = "0003_scheduler"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_chunks",
        sa.Column("chunk_id", sa.String(length=256), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.String(length=128), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_reference", sa.JSON(), nullable=False),
        sa.Column("embedding", VectorType(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["knowledge_documents.organization_id", "knowledge_documents.document_id"],
            name="fk_knowledge_chunks_org_document",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chunk_id"),
        sa.UniqueConstraint(
            "organization_id",
            "document_id",
            "chunk_index",
            name="uq_knowledge_chunks_order",
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_org_document",
        "knowledge_chunks",
        ["organization_id", "document_id"],
    )
    op.create_table(
        "knowledge_document_acl",
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.String(length=128), nullable=False),
        sa.Column("permission_code", sa.String(length=160), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["knowledge_documents.organization_id", "knowledge_documents.document_id"],
            name="fk_knowledge_document_acl_org_document",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("organization_id", "document_id", "permission_code"),
    )
    op.create_index(
        "ix_knowledge_document_acl_permission",
        "knowledge_document_acl",
        ["organization_id", "permission_code"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_document_acl_permission", table_name="knowledge_document_acl")
    op.drop_table("knowledge_document_acl")
    op.drop_index("ix_knowledge_chunks_org_document", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
