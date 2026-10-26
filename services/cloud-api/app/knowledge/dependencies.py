from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import Engine, and_, select

from app.persistence.postgres import (
    membership_roles,
    organization_memberships,
    permissions,
    role_permissions,
)

from .acl import KnowledgeAuthorizationContext, authorization_allows_retrieval

ACTIVE = "ACTIVE"

REASON_MEMBERSHIP_INACTIVE = "MEMBERSHIP_INACTIVE"
REASON_PERMISSION_REVOKED = "PERMISSION_REVOKED"
REASON_RBAC_UNAVAILABLE = "RBAC_UNAVAILABLE"
REASON_CONTEXT_INCOMPLETE = "CONTEXT_INCOMPLETE"


class KnowledgeAuthorizationDenied(Exception):
    """Raised when the server cannot prove the caller is trusted. Never carries secrets."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class MembershipSnapshot:
    """Server-side membership facts. Never constructed from a request body."""

    organization_id: str
    membership_id: str
    status: str
    scopes: tuple[str, ...]
    classifications: tuple[str, ...]
    permissions: tuple[str, ...]
    knowledge_permissions: tuple[str, ...]
    privacy: str
    budget_ref: str


class KnowledgeMembershipPort(Protocol):
    def current_membership(self, user_id: str) -> MembershipSnapshot | None: ...


class KnowledgeRBACPort(Protocol):
    def authorize(
        self,
        *,
        user_id: str,
        organization_id: str,
        permission: str,
        resource: Mapping[str, object] | None,
    ) -> bool: ...


def build_knowledge_context(
    user_id: str,
    snapshot: MembershipSnapshot | None,
    rbac: KnowledgeRBACPort,
    *,
    trace_id: str,
) -> KnowledgeAuthorizationContext:
    """Construct the authorization context only from server-computed facts."""
    if not isinstance(user_id, str) or not user_id.strip():
        raise KnowledgeAuthorizationDenied(REASON_MEMBERSHIP_INACTIVE)
    if not isinstance(trace_id, str) or not trace_id.strip():
        raise KnowledgeAuthorizationDenied(REASON_CONTEXT_INCOMPLETE)
    if snapshot is None or snapshot.status != ACTIVE:
        raise KnowledgeAuthorizationDenied(REASON_MEMBERSHIP_INACTIVE)
    for permission in snapshot.knowledge_permissions:
        try:
            allowed = rbac.authorize(
                user_id=user_id,
                organization_id=snapshot.organization_id,
                permission=permission,
                resource={"organization_id": snapshot.organization_id},
            )
        except Exception:
            raise KnowledgeAuthorizationDenied(REASON_RBAC_UNAVAILABLE) from None
        if not allowed:
            raise KnowledgeAuthorizationDenied(REASON_PERMISSION_REVOKED)
    context = KnowledgeAuthorizationContext(
        organization_id=snapshot.organization_id,
        subject_id=user_id,
        scopes=snapshot.scopes,
        classifications=snapshot.classifications,
        permissions=snapshot.permissions,
        knowledge_permissions=snapshot.knowledge_permissions,
        privacy=snapshot.privacy,
        budget_ref=snapshot.budget_ref,
        trace_id=trace_id,
    )
    if not authorization_allows_retrieval(context):
        raise KnowledgeAuthorizationDenied(REASON_CONTEXT_INCOMPLETE)
    return context


class PostgresMembershipPort:
    """Reads the ACTIVE membership and role-derived permission codes from Cloud PostgreSQL.

    Scope / classification / privacy / budget policy are injected server-side
    configuration; they are never taken from an HTTP request.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        scopes: Sequence[str],
        classifications: Sequence[str],
        privacy: str,
        budget_ref_template: str = "budget://{organization_id}/knowledge",
    ) -> None:
        self._engine = engine
        self._scopes = tuple(scopes)
        self._classifications = tuple(classifications)
        self._privacy = privacy
        self._budget_ref_template = budget_ref_template

    def current_membership(self, user_id: str) -> MembershipSnapshot | None:
        permission_join = (
            organization_memberships.join(
                membership_roles,
                and_(
                    organization_memberships.c.organization_id
                    == membership_roles.c.organization_id,
                    organization_memberships.c.membership_id
                    == membership_roles.c.membership_id,
                ),
            )
            .join(
                role_permissions,
                and_(
                    membership_roles.c.organization_id
                    == role_permissions.c.organization_id,
                    membership_roles.c.role_id == role_permissions.c.role_id,
                ),
            )
            .join(
                permissions,
                role_permissions.c.permission_id == permissions.c.permission_id,
            )
        )
        membership_statement = (
            select(
                organization_memberships.c.membership_id,
                organization_memberships.c.organization_id,
                permissions.c.permission_code,
            )
            .select_from(permission_join)
            .where(
                organization_memberships.c.user_id == user_id,
                organization_memberships.c.status == ACTIVE,
            )
        )
        with self._engine.connect() as connection:
            rows = connection.execute(membership_statement).all()
        if not rows:
            return None
        membership_id = rows[0].membership_id
        organization_id = rows[0].organization_id
        codes = tuple(dict.fromkeys(row.permission_code for row in rows))
        return MembershipSnapshot(
            organization_id=organization_id,
            membership_id=membership_id,
            status=ACTIVE,
            scopes=self._scopes,
            classifications=self._classifications,
            permissions=codes,
            knowledge_permissions=tuple(
                code for code in codes if code.startswith("knowledge.")
            ),
            privacy=self._privacy,
            budget_ref=self._budget_ref_template.format(organization_id=organization_id),
        )
