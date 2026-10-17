from datetime import UTC, datetime, timedelta

import pytest
from shuliu_runtime.approval.service import (
    ApprovalError,
    ApprovalService,
    ApprovalStatus,
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


def _context(**overrides):
    context = {
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
        "action_params": {"document_id": "doc-17", "content": "private body"},
        "run_id": "run-1",
        "trace_id": "trace-1",
    }
    context.update(overrides)
    return context


def _policy(decision=PolicyOutcome.REQUIRE_APPROVAL):
    return PolicyDecision(
        decision=decision,
        risk=RiskLevel.HIGH,
        rule_id="policy-rule-1",
        policy_version="policy-v1",
        reason="approval required",
        trace_id="trace-1",
    )


def _service(repository=None, *, now=None, authorized=None):
    clock_value = now or datetime(2026, 10, 17, tzinfo=UTC)
    return ApprovalService(
        repository=repository or MemoryApprovalRepository(),
        authorize_approver=authorized or (lambda **_: True),
        clock=lambda: clock_value,
        approval_ttl=timedelta(minutes=10),
    )


def test_request_requires_a_policy_decision_of_require_approval():
    service = _service()

    with pytest.raises(ApprovalError) as denied:
        service.request_approval(_context(), _policy(PolicyOutcome.DENY))

    assert denied.value.code == "POLICY_DENIED"

    with pytest.raises(ApprovalError) as allowed:
        service.request_approval(_context(), _policy(PolicyOutcome.ALLOW))

    assert allowed.value.code == "POLICY_APPROVAL_REQUIRED"

    with pytest.raises(ApprovalError) as missing:
        service.request_approval(_context(), None)
    assert missing.value.code == "POLICY_DECISION_INVALID"


def test_only_authorized_non_self_approver_can_approve_and_consume_once():
    repository = MemoryApprovalRepository()
    service = _service(repository)
    request = service.request_approval(_context(), _policy())

    with pytest.raises(ApprovalError) as self_approval:
        service.decide(request.approval_id, approver_id="agent-1", decision="APPROVE")
    assert self_approval.value.code == "APPROVAL_SELF_APPROVAL"
    assert repository.get(request.approval_id).status is ApprovalStatus.PENDING

    with pytest.raises(ApprovalError) as wrong_approver:
        service.decide(
            request.approval_id, approver_id="other-user", decision="APPROVE"
        )
    assert wrong_approver.value.code == "APPROVAL_NOT_AUTHORIZED"

    service.decide(
        request.approval_id,
        approver_id="user-approver",
        decision="APPROVE",
    )
    consumed = service.consume(request.approval_id, _context())

    assert consumed.status is ApprovalStatus.CONSUMED
    with pytest.raises(ApprovalError) as reused:
        service.consume(request.approval_id, _context())
    assert reused.value.code == "APPROVAL_NOT_CONSUMABLE"


def test_rejected_approval_never_becomes_consumable():
    service = _service()
    request = service.request_approval(_context(), _policy())

    service.decide(
        request.approval_id,
        approver_id="user-approver",
        decision="REJECT",
    )

    with pytest.raises(ApprovalError) as rejected:
        service.consume(request.approval_id, _context())
    assert rejected.value.code == "APPROVAL_REJECTED"


def test_approval_fails_closed_without_an_approver_authorizer():
    service = ApprovalService(
        repository=MemoryApprovalRepository(),
        authorize_approver=None,
        approval_ttl=timedelta(minutes=10),
    )
    request = service.request_approval(_context(), _policy())

    with pytest.raises(ApprovalError) as unauthorized:
        service.decide(
            request.approval_id,
            approver_id="user-approver",
            decision="APPROVE",
        )

    assert unauthorized.value.code == "APPROVAL_NOT_AUTHORIZED"


def test_expired_approval_is_recorded_and_cannot_be_consumed():
    created_at = datetime(2026, 10, 17, tzinfo=UTC)
    repository = MemoryApprovalRepository()
    service = _service(repository, now=created_at)
    request = service.request_approval(_context(), _policy())
    service.decide(
        request.approval_id,
        approver_id="user-approver",
        decision="APPROVE",
    )
    expired_service = ApprovalService(
        repository=repository,
        authorize_approver=lambda **_: True,
        clock=lambda: created_at + timedelta(minutes=11),
        approval_ttl=timedelta(minutes=10),
    )

    with pytest.raises(ApprovalError) as expired:
        expired_service.consume(request.approval_id, _context())

    assert expired.value.code == "APPROVAL_EXPIRED"
    assert expired_service.get(request.approval_id).status is ApprovalStatus.EXPIRED
