from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from shuliu_runtime.approval.service import (
    Approval,
    ApprovalAuditEvent,
    ApprovalError,
    ApprovalService,
    ApprovalStatus,
)
from shuliu_runtime.persistence.outbox import OutboxRepository
from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.policy.rules import PolicyDecision, PolicyOutcome
from shuliu_runtime.production.approvals import SQLiteApprovalRepository

ORGANIZATION_ID = "org-foundation"
REQUESTER_ID = "user-requester"
APPROVER_ID = "user-approver"
NOW = datetime(2026, 11, 3, 5, 0, tzinfo=UTC)


def approval_payload() -> dict[str, object]:
    return {
        "approval_id": "approval-1",
        "organization_id": ORGANIZATION_ID,
        "requester_id": REQUESTER_ID,
        "target_approver_id": APPROVER_ID,
        "action_id": "tool.publish",
        "action_summary": "Publish the approved page",
        "resource": {"type": "page", "id": "page-1"},
        "risk": RiskLevel.HIGH,
        "action_digest": "digest-1",
        "policy_decision": {"decision": "REQUIRE_APPROVAL", "rule_id": "rule-1"},
        "trace_id": "trace-1",
        "run_id": "run-1",
        "created_at": NOW,
        "expires_at": NOW + timedelta(hours=1),
    }


def approval(**overrides: object) -> Approval:
    return Approval(**{**approval_payload(), **overrides})  # type: ignore[arg-type]


def audit_event(approval_id: str, *, suffix: str) -> ApprovalAuditEvent:
    return ApprovalAuditEvent(
        event_id=f"event-{suffix}",
        idempotency_key=f"approval:{approval_id}:{suffix}",
        event_type=f"APPROVAL_{suffix.upper()}",
        organization_id=ORGANIZATION_ID,
        trace_id="trace-1",
        timestamp=NOW,
        payload={"approval_id": approval_id},
    )


@pytest.fixture()
def database(tmp_path) -> SQLiteDatabase:
    instance = SQLiteDatabase(tmp_path / "foundation.sqlite")
    instance.migrate()
    return instance


@pytest.fixture()
def repository(database: SQLiteDatabase) -> SQLiteApprovalRepository:
    return SQLiteApprovalRepository(database)


def test_a_created_approval_is_read_back_unchanged(repository: SQLiteApprovalRepository) -> None:
    created = approval()
    repository.create(created, audit_event(created.approval_id, suffix="requested"))

    assert repository.get("approval-1") == created


def test_an_approval_is_scoped_to_its_organization(repository: SQLiteApprovalRepository) -> None:
    created = approval()
    repository.create(created, audit_event(created.approval_id, suffix="requested"))

    assert repository.get("approval-1", "org-other") is None
    assert repository.get("approval-1", ORGANIZATION_ID) == created


def test_an_unknown_approval_is_absent(repository: SQLiteApprovalRepository) -> None:
    assert repository.get("approval-unknown") is None


def test_the_request_audit_event_reaches_the_outbox(
    repository: SQLiteApprovalRepository, database: SQLiteDatabase
) -> None:
    created = approval()
    repository.create(created, audit_event(created.approval_id, suffix="requested"))

    pending = OutboxRepository(database).pending()

    assert [record.event_id for record in pending] == ["event-requested"]
    # outbox 存的是项目统一的事件信封，业务字段位于 payload 之下。
    assert pending[0].payload["event_type"] == "APPROVAL_REQUESTED"
    assert pending[0].payload["payload"]["approval_id"] == "approval-1"


def test_a_transition_only_applies_to_the_expected_status(
    repository: SQLiteApprovalRepository,
) -> None:
    created = approval()
    repository.create(created, audit_event(created.approval_id, suffix="requested"))
    approved = Approval(
        **{
            **approval_payload(),
            "status": ApprovalStatus.APPROVED,
            "decision_by": APPROVER_ID,
            "decision_at": NOW,
        }
    )  # type: ignore[arg-type]

    applied = repository.transition(
        approved,
        expected_status=ApprovalStatus.PENDING,
        event=audit_event(created.approval_id, suffix="approved"),
    )
    stale = repository.transition(
        approved,
        expected_status=ApprovalStatus.PENDING,
        event=audit_event(created.approval_id, suffix="approved-again"),
    )

    assert applied is True
    assert stale is False
    assert repository.get("approval-1").status is ApprovalStatus.APPROVED


def test_a_rejected_transition_leaves_the_record_untouched(
    repository: SQLiteApprovalRepository, database: SQLiteDatabase
) -> None:
    created = approval()
    repository.create(created, audit_event(created.approval_id, suffix="requested"))
    approved = Approval(
        **{
            **approval_payload(),
            "status": ApprovalStatus.APPROVED,
            "decision_by": APPROVER_ID,
            "decision_at": NOW,
        }
    )  # type: ignore[arg-type]

    applied = repository.transition(
        approved,
        expected_status=ApprovalStatus.REJECTED,
        event=audit_event(created.approval_id, suffix="approved"),
    )

    assert applied is False
    assert repository.get("approval-1").status is ApprovalStatus.PENDING
    # 被拒绝的转换不得留下审计事件。
    assert len(OutboxRepository(database).pending()) == 1


def action_context() -> dict[str, object]:
    return {
        "organization_id": ORGANIZATION_ID,
        "requester_id": REQUESTER_ID,
        "target_approver_id": APPROVER_ID,
        "action_id": "tool.publish",
        "action_summary": "Publish the approved page",
        "resource": {"type": "page", "id": "page-1"},
        "risk": "HIGH",
        "trace_id": "trace-1",
        "run_id": "run-1",
    }


def policy_decision() -> PolicyDecision:
    return PolicyDecision(
        decision=PolicyOutcome.REQUIRE_APPROVAL,
        risk=RiskLevel.HIGH,
        rule_id="rule-1",
        policy_version="policy:v1",
        reason="HIGH_RISK_APPROVAL_REQUIRED",
        trace_id="trace-1",
    )


def service(repository: SQLiteApprovalRepository) -> ApprovalService:
    return ApprovalService(
        repository=repository,
        authorize_approver=lambda **_: True,
        approval_ttl=timedelta(hours=1),
        clock=lambda: NOW,
    )


def test_the_service_drives_the_full_lifecycle_over_the_sqlite_repository(
    repository: SQLiteApprovalRepository,
) -> None:
    approvals = service(repository)
    requested = approvals.request_approval(action_context(), policy_decision())

    approved = approvals.decide(
        requested.approval_id, approver_id=APPROVER_ID, decision="APPROVE"
    )
    consumed = approvals.consume(requested.approval_id, action_context())

    assert approved.status is ApprovalStatus.APPROVED
    assert consumed.status is ApprovalStatus.CONSUMED
    assert repository.get(requested.approval_id).status is ApprovalStatus.CONSUMED


def test_a_consumed_approval_cannot_be_consumed_twice(
    repository: SQLiteApprovalRepository,
) -> None:
    approvals = service(repository)
    requested = approvals.request_approval(action_context(), policy_decision())
    approvals.decide(requested.approval_id, approver_id=APPROVER_ID, decision="APPROVE")
    approvals.consume(requested.approval_id, action_context())

    with pytest.raises(ApprovalError) as error:
        approvals.consume(requested.approval_id, action_context())

    assert error.value.code == "APPROVAL_NOT_CONSUMABLE"
