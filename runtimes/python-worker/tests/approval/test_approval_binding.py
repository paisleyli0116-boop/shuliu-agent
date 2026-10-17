import hashlib
import json
from dataclasses import asdict
from datetime import timedelta

import pytest
from shuliu_runtime.approval.context_hash import action_context_digest
from shuliu_runtime.approval.service import (
    ApprovalError,
    ApprovalService,
)
from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.policy.rules import PolicyDecision, PolicyOutcome


class MemoryApprovalRepository:
    def __init__(self):
        self.records = {}
        self.events = []

    def create(self, approval, event):
        self.records[approval.approval_id] = approval
        self.events.append(event)

    def get(self, approval_id, organization_id=None):
        approval = self.records.get(approval_id)
        if approval is None or (
            organization_id is not None and approval.organization_id != organization_id
        ):
            return None
        return approval

    def transition(self, approval, expected_status, event):
        current = self.records.get(approval.approval_id)
        if current is None or current.status is not expected_status:
            return False
        self.records[approval.approval_id] = approval
        self.events.append(event)
        return True


def _context():
    return {
        "organization_id": "org-1",
        "requester_id": "agent-1",
        "target_approver_id": "user-approver",
        "action_id": "tool.publish",
        "action_summary": "Publish the approved result",
        "resource": {
            "organization_id": "org-1",
            "resource_type": "document",
            "resource_id": "doc-17",
            "display_label": "internal draft",
        },
        "risk": "HIGH",
        "action_params": {"document_id": "doc-17", "content": "private body"},
        "credentials": {"api_key": "private-api-key"},
        "run_id": "run-1",
        "trace_id": "trace-1",
    }


def _policy():
    return PolicyDecision(
        decision=PolicyOutcome.REQUIRE_APPROVAL,
        risk=RiskLevel.HIGH,
        rule_id="policy-rule-1",
        policy_version="policy-v1",
        reason="approval required",
        trace_id="trace-1",
    )


def test_context_digest_is_canonical_and_covers_nested_action_parameters():
    action = _context()
    reordered = dict(reversed(tuple(action.items())))

    assert action_context_digest(action) == action_context_digest(reordered)

    changed = _context()
    changed["action_params"] = {"document_id": "doc-17", "content": "changed"}
    assert action_context_digest(action) != action_context_digest(changed)


def test_context_digest_uses_sha256_of_canonical_json():
    action = _context()
    expected = hashlib.sha256(
        json.dumps(
            action,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    assert action_context_digest(action) == expected


def test_changed_context_invalidates_an_approved_request_and_audit_omits_parameters():
    repository = MemoryApprovalRepository()
    service = ApprovalService(
        repository=repository,
        authorize_approver=lambda **_: True,
        approval_ttl=timedelta(minutes=10),
    )
    request = service.request_approval(_context(), _policy())
    service.decide(request.approval_id, approver_id="user-approver", decision="APPROVE")
    changed = _context()
    changed["action_params"] = {"document_id": "doc-17", "content": "changed"}

    with pytest.raises(ApprovalError) as changed_context:
        service.consume(request.approval_id, changed)

    assert changed_context.value.code == "APPROVAL_CONTEXT_CHANGED"
    assert repository.get(request.approval_id).status.value == "INVALIDATED"
    audit_json = json.dumps([asdict(event) for event in repository.events], default=str)
    assert "private body" not in audit_json
    assert "private-api-key" not in audit_json
    assert "action_params" not in audit_json
