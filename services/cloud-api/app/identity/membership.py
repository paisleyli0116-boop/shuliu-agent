from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine, insert, update

from app.persistence.postgres import organization_memberships, users


ACTIVE = "ACTIVE"
REVOKED = "REVOKED"


def _required(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value


class MembershipRepository:
    """Persists users and their explicitly organization-scoped membership."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add_user(self, user_id: str) -> None:
        user_id = _required(user_id, "user_id")
        with self._engine.begin() as connection:
            connection.execute(
                insert(users).values(
                    user_id=user_id,
                    status=ACTIVE,
                    created_at=datetime.now(timezone.utc),
                )
            )

    def add_membership(
        self,
        *,
        membership_id: str,
        user_id: str,
        organization_id: str,
    ) -> None:
        membership_id = _required(membership_id, "membership_id")
        user_id = _required(user_id, "user_id")
        organization_id = _required(organization_id, "organization_id")
        with self._engine.begin() as connection:
            connection.execute(
                insert(organization_memberships).values(
                    membership_id=membership_id,
                    user_id=user_id,
                    organization_id=organization_id,
                    status=ACTIVE,
                    created_at=datetime.now(timezone.utc),
                )
            )

    def revoke_membership(self, *, membership_id: str, organization_id: str) -> bool:
        membership_id = _required(membership_id, "membership_id")
        organization_id = _required(organization_id, "organization_id")
        with self._engine.begin() as connection:
            result = connection.execute(
                update(organization_memberships)
                .where(
                    organization_memberships.c.membership_id == membership_id,
                    organization_memberships.c.organization_id == organization_id,
                    organization_memberships.c.status == ACTIVE,
                )
                .values(status=REVOKED)
            )
            return result.rowcount == 1
