from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from sqlalchemy import Engine, and_, delete, insert, select

from app.persistence.postgres import (
    membership_roles,
    organization_memberships,
    permissions,
    role_permissions,
    roles,
    users,
)
from app.identity.membership import ACTIVE, _required


class IdentityAssignmentError(ValueError):
    """Raised when a role or permission grant crosses an identity boundary."""


class RBACRepository:
    """Organization-scoped role and permission grants used by Cloud authorization."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_role(self, *, role_id: str, organization_id: str, name: str) -> None:
        role_id = _required(role_id, "role_id")
        organization_id = _required(organization_id, "organization_id")
        name = _required(name, "name")
        with self._engine.begin() as connection:
            connection.execute(
                insert(roles).values(
                    role_id=role_id,
                    organization_id=organization_id,
                    name=name,
                    status=ACTIVE,
                    created_at=datetime.now(timezone.utc),
                )
            )

    def create_permission(self, *, permission_id: str, permission_code: str) -> None:
        permission_id = _required(permission_id, "permission_id")
        permission_code = _required(permission_code, "permission_code")
        with self._engine.begin() as connection:
            connection.execute(
                insert(permissions).values(
                    permission_id=permission_id,
                    permission_code=permission_code,
                    created_at=datetime.now(timezone.utc),
                )
            )

    def grant_permission(
        self, *, organization_id: str, role_id: str, permission_id: str
    ) -> None:
        organization_id = _required(organization_id, "organization_id")
        role_id = _required(role_id, "role_id")
        permission_id = _required(permission_id, "permission_id")
        with self._engine.begin() as connection:
            role_exists = connection.execute(
                select(roles.c.role_id).where(
                    roles.c.organization_id == organization_id,
                    roles.c.role_id == role_id,
                    roles.c.status == ACTIVE,
                )
            ).scalar_one_or_none()
            permission_exists = connection.execute(
                select(permissions.c.permission_id).where(
                    permissions.c.permission_id == permission_id
                )
            ).scalar_one_or_none()
            if role_exists is None or permission_exists is None:
                raise IdentityAssignmentError("role or permission is unavailable")
            connection.execute(
                insert(role_permissions).values(
                    organization_id=organization_id,
                    role_id=role_id,
                    permission_id=permission_id,
                    created_at=datetime.now(timezone.utc),
                )
            )

    def assign_role(
        self,
        *,
        organization_id: str,
        membership_id: str,
        role_id: str,
    ) -> None:
        organization_id = _required(organization_id, "organization_id")
        membership_id = _required(membership_id, "membership_id")
        role_id = _required(role_id, "role_id")
        with self._engine.begin() as connection:
            active_membership = connection.execute(
                select(organization_memberships.c.membership_id).where(
                    organization_memberships.c.organization_id == organization_id,
                    organization_memberships.c.membership_id == membership_id,
                    organization_memberships.c.status == ACTIVE,
                )
            ).scalar_one_or_none()
            active_role = connection.execute(
                select(roles.c.role_id).where(
                    roles.c.organization_id == organization_id,
                    roles.c.role_id == role_id,
                    roles.c.status == ACTIVE,
                )
            ).scalar_one_or_none()
            if active_membership is None or active_role is None:
                raise IdentityAssignmentError(
                    "active membership and role in the same organization are required"
                )
            connection.execute(
                insert(membership_roles).values(
                    organization_id=organization_id,
                    membership_id=membership_id,
                    role_id=role_id,
                    created_at=datetime.now(timezone.utc),
                )
            )

    def revoke_role(
        self, *, organization_id: str, membership_id: str, role_id: str
    ) -> bool:
        organization_id = _required(organization_id, "organization_id")
        membership_id = _required(membership_id, "membership_id")
        role_id = _required(role_id, "role_id")
        with self._engine.begin() as connection:
            result = connection.execute(
                delete(membership_roles).where(
                    membership_roles.c.organization_id == organization_id,
                    membership_roles.c.membership_id == membership_id,
                    membership_roles.c.role_id == role_id,
                )
            )
            return result.rowcount == 1

    def authorize(
        self,
        *,
        user_id: str,
        organization_id: str,
        permission: str,
        resource: Mapping[str, object] | None,
    ) -> bool:
        values = (user_id, organization_id, permission)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            return False
        if resource is not None:
            if not isinstance(resource, Mapping):
                return False
            resource_organization_id = resource.get("organization_id")
            if (
                "organization_id" in resource
                and resource_organization_id != organization_id
            ):
                return False

        membership_role_join = organization_memberships.join(
            membership_roles,
            and_(
                organization_memberships.c.organization_id
                == membership_roles.c.organization_id,
                organization_memberships.c.membership_id
                == membership_roles.c.membership_id,
            ),
        )
        role_join = membership_role_join.join(
            roles,
            and_(
                membership_roles.c.organization_id == roles.c.organization_id,
                membership_roles.c.role_id == roles.c.role_id,
            ),
        )
        permission_join = role_join.join(
            role_permissions,
            and_(
                roles.c.organization_id == role_permissions.c.organization_id,
                roles.c.role_id == role_permissions.c.role_id,
            ),
        ).join(
            permissions,
            role_permissions.c.permission_id == permissions.c.permission_id,
        )
        identity_join = users.join(
            permission_join,
            users.c.user_id == organization_memberships.c.user_id,
        )
        statement = (
            select(users.c.user_id)
            .select_from(identity_join)
            .where(
                users.c.user_id == user_id,
                users.c.status == ACTIVE,
                organization_memberships.c.organization_id == organization_id,
                organization_memberships.c.status == ACTIVE,
                roles.c.organization_id == organization_id,
                roles.c.status == ACTIVE,
                role_permissions.c.organization_id == organization_id,
                permissions.c.permission_code == permission,
            )
            .limit(1)
        )
        with self._engine.connect() as connection:
            return connection.execute(statement).first() is not None
