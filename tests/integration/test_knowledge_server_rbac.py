from __future__ import annotations

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from app.identity.membership import MembershipRepository
from app.identity.rbac import RBACRepository
from app.knowledge.audit import (
    VERIFIED,
    KnowledgeAuditRecord,
    PostgresKnowledgeAuditSink,
)
from app.knowledge.dependencies import (
    KnowledgeAuthorizationDenied,
    PostgresMembershipPort,
    build_knowledge_context,
)
from app.persistence.postgres import create_postgres_engine, events

pytestmark = pytest.mark.postgres


def _database_url() -> str:
    database_url = os.environ.get("SHULIU_DATABASE_URL")
    if not database_url:
        pytest.skip("SHULIU_DATABASE_URL is not configured")
    return database_url


def _migrate(engine: object) -> None:
    config = Config("services/cloud-api/alembic.ini")
    with engine.begin() as connection:  # type: ignore[attr-defined]
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


@pytest.mark.integration
def test_server_rbac_membership_and_revocation_drive_knowledge_context():
    engine = create_postgres_engine(_database_url())
    _migrate(engine)

    user_id = f"user-{uuid4()}"
    organization_id = f"org-{uuid4()}"
    membership_id = f"membership-{uuid4()}"
    role_id = f"role-{uuid4()}"
    permission_id = f"permission-{uuid4()}"
    trace_id = f"trace-{uuid4()}"

    memberships = MembershipRepository(engine)
    memberships.add_user(user_id)
    memberships.add_membership(
        membership_id=membership_id,
        user_id=user_id,
        organization_id=organization_id,
    )
    rbac = RBACRepository(engine)
    rbac.create_role(role_id=role_id, organization_id=organization_id, name="knowledge-reader")
    rbac.create_permission(permission_id=permission_id, permission_code="knowledge.read")
    rbac.grant_permission(
        organization_id=organization_id, role_id=role_id, permission_id=permission_id
    )
    rbac.assign_role(
        organization_id=organization_id,
        membership_id=membership_id,
        role_id=role_id,
    )

    port = PostgresMembershipPort(
        engine,
        scopes=("team:finance",),
        classifications=("INTERNAL",),
        privacy="INTERNAL",
    )
    snapshot = port.current_membership(user_id)
    assert snapshot is not None
    assert snapshot.organization_id == organization_id
    assert snapshot.status == "ACTIVE"
    assert snapshot.knowledge_permissions == ("knowledge.read",)

    context = build_knowledge_context(
        user_id, snapshot, rbac, trace_id=trace_id
    )
    assert context.organization_id == organization_id
    assert context.subject_id == user_id
    assert context.knowledge_permissions == ("knowledge.read",)
    assert context.trace_id == trace_id

    # Role revocation must deny the live permission check (fail closed).
    assert rbac.revoke_role(
        organization_id=organization_id,
        membership_id=membership_id,
        role_id=role_id,
    )
    with pytest.raises(KnowledgeAuthorizationDenied):
        build_knowledge_context(
            user_id, port.current_membership(user_id), rbac, trace_id=f"trace-{uuid4()}"
        )

    # Membership revocation removes the trust anchor entirely.
    memberships.revoke_membership(
        membership_id=membership_id,
        organization_id=organization_id,
    )
    assert port.current_membership(user_id) is None
    with pytest.raises(KnowledgeAuthorizationDenied):
        build_knowledge_context(user_id, None, rbac, trace_id=f"trace-{uuid4()}")


@pytest.mark.integration
def test_knowledge_audit_sink_appends_trace_records_into_events():
    engine = create_postgres_engine(_database_url())
    _migrate(engine)

    organization_id = f"org-{uuid4()}"
    trace_id = f"trace-{uuid4()}"
    record = KnowledgeAuditRecord(
        organization_id=organization_id,
        subject_id=f"user-{uuid4()}",
        trace_id=trace_id,
        outcome=VERIFIED,
        policy_version="knowledge-policy-v1",
        document_refs=({"document_id": "doc-1", "chunk_id": "chunk-1"},),
    )
    PostgresKnowledgeAuditSink(engine).append(record)
    with engine.connect() as connection:
        rows = connection.execute(
            events.select().where(events.c.organization_id == organization_id)
        ).mappings().all()
    assert len(rows) == 1
    assert rows[0]["payload"]["trace_id"] == trace_id
    assert rows[0]["payload"]["outcome"] == VERIFIED
    assert rows[0]["idempotency_key"] == f"knowledge-audit:{trace_id}:{VERIFIED}"
