from datetime import UTC, datetime

from fastapi.testclient import TestClient
from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.policy.rules import PolicyDecision, PolicyOutcome


def test_approval_list_and_decision_are_identity_and_permission_scoped(cloud_api):
    app, _ = cloud_api
    approval = app.state.approval_service.request_approval(
        {
            "organization_id": "org-1",
            "requester_id": "agent-system",
            "target_approver_id": "user-1",
            "action_id": "foundation.generic-action",
            "action_summary": "Review generic action",
            "resource": {"organization_id": "org-1", "resource_type": "task"},
            "risk": "HIGH",
            "action_params": {"opaque": "private"},
            "trace_id": "trace-cloud-api",
        },
        PolicyDecision(
            decision=PolicyOutcome.REQUIRE_APPROVAL,
            risk=RiskLevel.HIGH,
            rule_id="rule-1",
            policy_version="policy-v1",
            reason="human decision required",
            trace_id="trace-cloud-api",
        ),
    )
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer user-1-token",
        "X-Organization-ID": "org-1",
    }

    listing = client.get("/v1/approvals", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["items"][0]["approval_id"] == approval.approval_id
    assert "private" not in listing.text

    decided = client.post(
        f"/v1/approvals/{approval.approval_id}/decision",
        headers=headers,
        json={"decision": "APPROVE"},
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "APPROVED"


def test_approval_decision_rejects_missing_authentication(cloud_api):
    app, _ = cloud_api
    response = TestClient(app).post(
        "/v1/approvals/approval-missing/decision", json={"decision": "APPROVE"}
    )
    assert response.status_code == 401
