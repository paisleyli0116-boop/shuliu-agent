"""Add single-company identity and organization-scoped RBAC tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_identity_rbac"
down_revision = "0001_foundation_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "organization_memberships",
        sa.Column("membership_id", sa.String(length=128), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.user_id"], name="fk_memberships_user"
        ),
        sa.PrimaryKeyConstraint("membership_id"),
        sa.UniqueConstraint(
            "organization_id", "membership_id", name="uq_memberships_org_membership"
        ),
        sa.UniqueConstraint(
            "organization_id", "user_id", name="uq_memberships_org_user"
        ),
    )
    op.create_table(
        "roles",
        sa.Column("role_id", sa.String(length=128), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("role_id"),
        sa.UniqueConstraint("organization_id", "role_id", name="uq_roles_org_role"),
        sa.UniqueConstraint("organization_id", "name", name="uq_roles_org_name"),
    )
    op.create_table(
        "permissions",
        sa.Column("permission_id", sa.String(length=128), nullable=False),
        sa.Column("permission_code", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("permission_id"),
        sa.UniqueConstraint("permission_code", name="uq_permissions_code"),
    )
    op.create_table(
        "membership_roles",
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("membership_id", sa.String(length=128), nullable=False),
        sa.Column("role_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.membership_id"],
            name="fk_membership_roles_org_membership",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "role_id"],
            ["roles.organization_id", "roles.role_id"],
            name="fk_membership_roles_org_role",
        ),
        sa.PrimaryKeyConstraint("organization_id", "membership_id", "role_id"),
    )
    op.create_table(
        "role_permissions",
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("role_id", sa.String(length=128), nullable=False),
        sa.Column("permission_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "role_id"],
            ["roles.organization_id", "roles.role_id"],
            name="fk_role_permissions_org_role",
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["permissions.permission_id"],
            name="fk_role_permissions_permission",
        ),
        sa.PrimaryKeyConstraint("organization_id", "role_id", "permission_id"),
    )
    op.create_index(
        "ix_memberships_org_user_status",
        "organization_memberships",
        ["organization_id", "user_id", "status"],
    )
    op.create_index(
        "ix_membership_roles_org_role",
        "membership_roles",
        ["organization_id", "role_id"],
    )
    op.create_index(
        "ix_role_permissions_org_permission",
        "role_permissions",
        ["organization_id", "permission_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_role_permissions_org_permission", table_name="role_permissions")
    op.drop_index("ix_membership_roles_org_role", table_name="membership_roles")
    op.drop_index("ix_memberships_org_user_status", table_name="organization_memberships")
    op.drop_table("role_permissions")
    op.drop_table("membership_roles")
    op.drop_table("permissions")
    op.drop_table("roles")
    op.drop_table("organization_memberships")
    op.drop_table("users")
