from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.identity.membership import MembershipRepository
from app.identity.provider import FakeIdentityProvider
from app.identity.rbac import RBACRepository
from app.persistence.postgres import metadata
from app.main import create_app
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
def cloud_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    memberships = MembershipRepository(engine)
    memberships.add_user("user-1")
    memberships.add_membership(
        membership_id="membership-1", user_id="user-1", organization_id="org-1"
    )
    memberships.add_user("user-2")
    memberships.add_membership(
        membership_id="membership-2", user_id="user-2", organization_id="org-2"
    )
    rbac = RBACRepository(engine)
    for permission in (
        "foundation.task.create",
        "foundation.task.read",
        "foundation.approval.read",
        "foundation.approval.decide",
    ):
        rbac.create_permission(permission_id=permission, permission_code=permission)
    for org, membership in (("org-1", "membership-1"), ("org-2", "membership-2")):
        role_id = f"role-{org}"
        rbac.create_role(role_id=role_id, organization_id=org, name="foundation-user")
        rbac.assign_role(organization_id=org, membership_id=membership, role_id=role_id)
        for permission in (
            "foundation.task.create",
            "foundation.task.read",
            "foundation.approval.read",
            "foundation.approval.decide",
        ):
            rbac.grant_permission(
                organization_id=org,
                role_id=role_id,
                permission_id=permission,
            )
    provider = FakeIdentityProvider(
        credential_users={"user-1-token": "user-1", "user-2-token": "user-2"},
        session_ttl=timedelta(hours=1),
        clock=lambda: datetime(2026, 10, 18, tzinfo=UTC),
    )
    app = create_app(
        engine=engine,
        session_secret_resolver=lambda: b"test-only-worker-session-key-32bytes!",
        clock=lambda: datetime(2026, 10, 18, tzinfo=UTC),
        identity_provider=provider,
        rbac_repository=rbac,
    )
    try:
        yield app, engine
    finally:
        engine.dispose()
