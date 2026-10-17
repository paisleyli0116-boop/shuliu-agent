from datetime import UTC, datetime, timedelta

import pytest
from app.approval.routes import PostgresApprovalRepository, create_approval_router
from app.persistence.postgres import approvals, events, metadata
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shuliu_runtime.approval.service import ApprovalService
from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.policy.rules import PolicyDecision, PolicyOutcome
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool


@pytest.fixture
def approval_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    repository = PostgresApprovalRepository(engine)
    now = datetime(2026, 10, 17, tzinfo=UTC)
    service = ApprovalService(
        repository=repository,
        authorize_approver=lambda **values: values["approver_id"] == "user-approver",
        clock=lambda: now,
        approval_ttl=timedelta(minutes=10),
    )
    action_context = {
        "organization_id": "org-1",
        "requester_id": "agent-1",
        "target_approver_id": "user-approver",
        "action_id": "tool.publish",
        "action_summary": "Publish the approved result",
        "resource": {
            "organization_id": "org-1",
            "resource_type": "document",
            "resource_id": "doc-17",
        },
        "risk": "HIGH",
        "action_params": {"content": "secret document text"},
        "run_id": None,
        "trace_id": "trace-1",
    }
    policy_decision = PolicyDecision(
        decision=PolicyOutcome.REQUIRE_APPROVAL,
        risk=RiskLevel.HIGH,
        rule_id="policy-rule-1",
        policy_version="policy-v1",
        reason="approval required",
        trace_id="trace-1",
    )
    approval = service.request_approval(action_context, policy_decision)
    active_user = {"user_id": "intruder"}

    def current_user():
        return active_user["user_id"]

    app = FastAPI()
    app.include_router(create_approval_router(service, current_user))
    try:
        yield TestClient(app), service, approval, active_user, engine
    finally:
        engine.dispose()


def test_unauthorized_user_cannot_decide_and_approval_stays_pending(approval_api):
    client, service, approval, active_user, _ = approval_api

    response = client.post(
        f"/approvals/{approval.approval_id}/decision",
        json={"decision": "APPROVE"},
    )

    assert response.status_code == 403, response.text
    assert service.get(approval.approval_id).status.value == "PENDING"
    active_user["user_id"] = "user-approver"
    approved = client.post(
        f"/approvals/{approval.approval_id}/decision",
        json={"decision": "APPROVE"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"


def test_approval_repository_persists_state_and_redacted_audit_events(approval_api):
    client, service, approval, active_user, engine = approval_api
    active_user["user_id"] = "user-approver"
    response = client.post(
        f"/approvals/{approval.approval_id}/decision",
        json={"decision": "REJECT"},
    )
    assert response.status_code == 200

    with engine.connect() as connection:
        saved = connection.execute(
            select(approvals.c.status).where(
                approvals.c.approval_id == approval.approval_id
            )
        ).scalar_one()
        audit_payloads = connection.execute(select(events.c.payload)).all()

    assert saved == "REJECTED"
    audit_json = str(audit_payloads)
    assert "secret document text" not in audit_json
    assert "action_params" not in audit_json
    assert service.get(approval.approval_id).status.value == "REJECTED"
    restarted_service = ApprovalService(
        repository=PostgresApprovalRepository(engine),
        authorize_approver=lambda **_: True,
        clock=lambda: datetime(2026, 10, 17, tzinfo=UTC),
        approval_ttl=timedelta(minutes=10),
    )
    assert restarted_service.get(approval.approval_id).status.value == "REJECTED"


def test_route_does_not_return_action_parameters(approval_api):
    client, _, approval, active_user, _ = approval_api
    active_user["user_id"] = "user-approver"

    response = client.post(
        f"/approvals/{approval.approval_id}/decision",
        json={"decision": "APPROVE"},
    )

    assert response.status_code == 200
    assert "action_params" not in response.json()
    assert "secret document text" not in response.text
