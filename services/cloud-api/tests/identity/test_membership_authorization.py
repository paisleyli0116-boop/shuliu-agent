import pytest
from sqlalchemy import create_engine

from app.identity.membership import MembershipRepository
from app.identity.rbac import IdentityAssignmentError, RBACRepository
from app.persistence.postgres import metadata


@pytest.fixture
def identity_repositories():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    try:
        yield MembershipRepository(engine), RBACRepository(engine)
    finally:
        engine.dispose()


def _grant_read(memberships, rbac, user_id="user-1", organization_id="org-a"):
    memberships.add_user(user_id)
    membership_id = f"membership-{user_id}-{organization_id}"
    memberships.add_membership(
        membership_id=membership_id,
        user_id=user_id,
        organization_id=organization_id,
    )
    rbac.create_role(
        role_id=f"role-{organization_id}",
        organization_id=organization_id,
        name="foundation-reader",
    )
    rbac.create_permission(
        permission_id="permission-task-read",
        permission_code="foundation.task.read",
    )
    rbac.grant_permission(
        organization_id=organization_id,
        role_id=f"role-{organization_id}",
        permission_id="permission-task-read",
    )
    rbac.assign_role(
        organization_id=organization_id,
        membership_id=membership_id,
        role_id=f"role-{organization_id}",
    )
    return membership_id, f"role-{organization_id}"


def test_unknown_user_and_user_without_membership_are_denied(identity_repositories):
    memberships, rbac = identity_repositories
    memberships.add_user("user-without-membership")

    assert not rbac.authorize(
        user_id="missing-user",
        organization_id="org-a",
        permission="foundation.task.read",
        resource={"organization_id": "org-a"},
    )
    assert not rbac.authorize(
        user_id="user-without-membership",
        organization_id="org-a",
        permission="foundation.task.read",
        resource={"organization_id": "org-a"},
    )


def test_permission_requires_active_membership_and_matching_organization(
    identity_repositories,
):
    memberships, rbac = identity_repositories
    _grant_read(memberships, rbac)

    assert rbac.authorize(
        user_id="user-1",
        organization_id="org-a",
        permission="foundation.task.read",
        resource={"organization_id": "org-a", "resource_id": "task-1"},
    )
    assert not rbac.authorize(
        user_id="user-1",
        organization_id="org-b",
        permission="foundation.task.read",
        resource={"organization_id": "org-b", "resource_id": "task-1"},
    )
    assert not rbac.authorize(
        user_id="user-1",
        organization_id="org-a",
        permission="foundation.task.read",
        resource={"organization_id": "org-b", "resource_id": "task-1"},
    )
    assert not rbac.authorize(
        user_id="user-1",
        organization_id="org-a",
        permission="foundation.task.read",
        resource={"organization_id": None, "resource_id": "task-1"},
    )


def test_revoking_role_or_membership_immediately_removes_access(identity_repositories):
    memberships, rbac = identity_repositories
    membership_id, role_id = _grant_read(memberships, rbac)
    resource = {"organization_id": "org-a", "resource_id": "task-1"}
    authorization = {
        "user_id": "user-1",
        "organization_id": "org-a",
        "permission": "foundation.task.read",
        "resource": resource,
    }

    assert rbac.authorize(**authorization)
    assert rbac.revoke_role(
        organization_id="org-a",
        membership_id=membership_id,
        role_id=role_id,
    )
    assert not rbac.authorize(**authorization)

    rbac.assign_role(
        organization_id="org-a",
        membership_id=membership_id,
        role_id=role_id,
    )
    assert memberships.revoke_membership(
        membership_id=membership_id,
        organization_id="org-a",
    )
    assert not rbac.authorize(**authorization)


def test_role_assignment_cannot_cross_organization_boundaries(identity_repositories):
    memberships, rbac = identity_repositories
    membership_id, _ = _grant_read(memberships, rbac)
    rbac.create_role(
        role_id="role-org-b",
        organization_id="org-b",
        name="foundation-reader",
    )

    with pytest.raises(IdentityAssignmentError):
        rbac.assign_role(
            organization_id="org-b",
            membership_id=membership_id,
            role_id="role-org-b",
        )
