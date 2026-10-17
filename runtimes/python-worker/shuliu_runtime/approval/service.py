from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Protocol
from uuid import uuid4

from shuliu_runtime.approval.context_hash import action_context_digest
from shuliu_runtime.policy.risk import RiskLevel, parse_risk_level
from shuliu_runtime.policy.rules import PolicyDecision, PolicyOutcome


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    CONSUMED = "CONSUMED"


class ApprovalError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Approval:
    approval_id: str
    organization_id: str
    requester_id: str
    target_approver_id: str
    action_id: str
    action_summary: str
    resource: Mapping[str, object]
    risk: RiskLevel
    action_digest: str
    policy_decision: Mapping[str, object]
    trace_id: str
    run_id: str | None
    created_at: datetime
    expires_at: datetime
    status: ApprovalStatus = ApprovalStatus.PENDING
    decision_by: str | None = None
    decision_at: datetime | None = None


@dataclass(frozen=True)
class ApprovalAuditEvent:
    event_id: str
    idempotency_key: str
    event_type: str
    organization_id: str
    trace_id: str
    timestamp: datetime
    payload: Mapping[str, object]


class ApprovalRepository(Protocol):
    def create(self, approval: Approval, event: ApprovalAuditEvent) -> None: ...

    def get(
        self, approval_id: str, organization_id: str | None = None
    ) -> Approval | None: ...

    def transition(
        self,
        approval: Approval,
        expected_status: ApprovalStatus,
        event: ApprovalAuditEvent,
    ) -> bool: ...


ApproverAuthorizer = Callable[..., bool]


class ApprovalService:
    """Context-bound, fail-closed approval lifecycle independent of executors."""

    def __init__(
        self,
        *,
        repository: ApprovalRepository,
        authorize_approver: ApproverAuthorizer | None,
        approval_ttl: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if approval_ttl <= timedelta(0):
            raise ValueError("approval_ttl must be positive")
        self._repository = repository
        self._authorize_approver = authorize_approver
        self._approval_ttl = approval_ttl
        self._clock = clock or (lambda: datetime.now(UTC))

    def request_approval(
        self,
        action_context: Mapping[str, object],
        policy_decision: PolicyDecision,
    ) -> Approval:
        context = _validated_context(action_context)
        decision = _validated_policy_decision(policy_decision, context)
        risk = parse_risk_level(context["risk"])
        if decision.decision is PolicyOutcome.DENY:
            raise ApprovalError("POLICY_DENIED", "Policy denied this action")
        if decision.decision is not PolicyOutcome.REQUIRE_APPROVAL:
            if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                raise ApprovalError(
                    "POLICY_APPROVAL_REQUIRED",
                    "High-risk actions require an explicit approval decision",
                )
            raise ApprovalError(
                "APPROVAL_NOT_REQUIRED", "Policy did not require approval"
            )

        now = _aware_utc(self._clock(), "clock")
        safe_resource = _safe_resource(context["resource"])
        approval = Approval(
            approval_id=str(uuid4()),
            organization_id=str(context["organization_id"]),
            requester_id=str(context["requester_id"]),
            target_approver_id=str(context["target_approver_id"]),
            action_id=str(context["action_id"]),
            action_summary=str(context["action_summary"]),
            resource=safe_resource,
            risk=risk,
            action_digest=action_context_digest(context),
            policy_decision={
                "decision": decision.decision.value,
                "risk": decision.risk.value if decision.risk else None,
                "rule_id": decision.rule_id,
                "policy_version": decision.policy_version,
                "reason": decision.reason,
                "trace_id": decision.trace_id,
            },
            trace_id=str(context["trace_id"]),
            run_id=_optional_text(context.get("run_id")),
            created_at=now,
            expires_at=now + self._approval_ttl,
        )
        event = self._event(
            approval,
            "APPROVAL_REQUESTED",
            now,
            {
                "requester_id": approval.requester_id,
                "target_approver_id": approval.target_approver_id,
                "action_id": approval.action_id,
                "action_summary": approval.action_summary,
                "resource": dict(approval.resource),
                "risk": approval.risk.value,
                "expires_at": approval.expires_at.isoformat(),
                "policy_decision": dict(approval.policy_decision),
                "action_digest": approval.action_digest,
            },
        )
        self._repository.create(approval, event)
        return approval

    def decide(
        self,
        approval_id: str,
        *,
        approver_id: str,
        decision: str,
    ) -> Approval:
        try:
            approver_id = _required_text(approver_id, "approver_id")
        except ValueError as error:
            raise ApprovalError(
                "APPROVAL_NOT_AUTHORIZED", "An authenticated approver is required"
            ) from error
        if decision not in ("APPROVE", "REJECT"):
            raise ApprovalError(
                "APPROVAL_DECISION_INVALID", "Unknown approval decision"
            )
        approval = self.get(approval_id)
        now = _aware_utc(self._clock(), "clock")
        if approval.status is not ApprovalStatus.PENDING:
            raise _status_error(approval.status)
        if now >= approval.expires_at:
            self._transition(
                approval,
                ApprovalStatus.EXPIRED,
                now,
                "APPROVAL_EXPIRED",
                {
                    "reason": "approval window elapsed",
                },
            )
            raise ApprovalError("APPROVAL_EXPIRED", "Approval request has expired")
        if approver_id == approval.requester_id:
            raise ApprovalError(
                "APPROVAL_SELF_APPROVAL", "The requester cannot approve its own action"
            )
        if approver_id != approval.target_approver_id:
            raise ApprovalError(
                "APPROVAL_NOT_AUTHORIZED", "Approver is not the assigned approver"
            )
        authorized = (
            self._authorize_approver(
                organization_id=approval.organization_id,
                approver_id=approver_id,
                permission="foundation.approval.decide",
                resource=dict(approval.resource),
            )
            if self._authorize_approver is not None
            else False
        )
        if authorized is not True:
            raise ApprovalError(
                "APPROVAL_NOT_AUTHORIZED", "Approver lacks the required permission"
            )

        status = (
            ApprovalStatus.APPROVED
            if decision == "APPROVE"
            else ApprovalStatus.REJECTED
        )
        updated = replace(
            approval,
            status=status,
            decision_by=approver_id,
            decision_at=now,
        )
        event_type = (
            "APPROVAL_APPROVED" if decision == "APPROVE" else "APPROVAL_REJECTED"
        )
        event = self._event(
            updated,
            event_type,
            now,
            {
                "decision_by": approver_id,
                "decision": decision,
            },
        )
        if not self._repository.transition(
            approval=updated, expected_status=ApprovalStatus.PENDING, event=event
        ):
            raise ApprovalError(
                "APPROVAL_STATE_CONFLICT", "Approval state changed concurrently"
            )
        return updated

    def consume(
        self, approval_id: str, action_context: Mapping[str, object]
    ) -> Approval:
        context = _validated_context(action_context)
        approval = self.get(
            approval_id, organization_id=str(context["organization_id"])
        )
        digest = action_context_digest(context)
        now = _aware_utc(self._clock(), "clock")
        if digest != approval.action_digest:
            if approval.status in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED):
                self._transition(
                    approval,
                    ApprovalStatus.INVALIDATED,
                    now,
                    "APPROVAL_INVALIDATED",
                    {
                        "reason": "action context changed",
                        "expected_digest": approval.action_digest,
                        "received_digest": digest,
                    },
                )
            raise ApprovalError(
                "APPROVAL_CONTEXT_CHANGED",
                "Action context changed; policy must be evaluated again",
            )
        if (
            approval.status in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED)
            and now >= approval.expires_at
        ):
            self._transition(
                approval,
                ApprovalStatus.EXPIRED,
                now,
                "APPROVAL_EXPIRED",
                {
                    "reason": "approval window elapsed",
                },
            )
            raise ApprovalError("APPROVAL_EXPIRED", "Approval request has expired")
        if approval.status is ApprovalStatus.REJECTED:
            raise ApprovalError(
                "APPROVAL_REJECTED", "Rejected action cannot be retried"
            )
        if approval.status is not ApprovalStatus.APPROVED:
            raise _status_error(approval.status)

        consumed = replace(approval, status=ApprovalStatus.CONSUMED)
        event = self._event(
            consumed,
            "APPROVAL_CONSUMED",
            now,
            {
                "decision_by": approval.decision_by,
                "action_digest": approval.action_digest,
            },
        )
        if not self._repository.transition(
            approval=consumed, expected_status=ApprovalStatus.APPROVED, event=event
        ):
            raise ApprovalError(
                "APPROVAL_STATE_CONFLICT", "Approval state changed concurrently"
            )
        return consumed

    def get(self, approval_id: str, organization_id: str | None = None) -> Approval:
        approval = self._repository.get(approval_id, organization_id)
        if approval is None:
            raise ApprovalError("APPROVAL_NOT_FOUND", "Approval request was not found")
        return approval

    @staticmethod
    def _event(
        approval: Approval,
        event_type: str,
        timestamp: datetime,
        payload: Mapping[str, object],
    ) -> ApprovalAuditEvent:
        return ApprovalAuditEvent(
            event_id=str(uuid4()),
            idempotency_key=f"approval:{approval.approval_id}:{event_type.lower()}",
            event_type=event_type,
            organization_id=approval.organization_id,
            trace_id=approval.trace_id,
            timestamp=timestamp,
            payload={"approval_id": approval.approval_id, **payload},
        )

    def _transition(
        self,
        approval: Approval,
        status: ApprovalStatus,
        now: datetime,
        event_type: str,
        payload: Mapping[str, object],
    ) -> Approval:
        updated = replace(approval, status=status)
        event = self._event(updated, event_type, now, payload)
        if not self._repository.transition(
            approval=updated, expected_status=approval.status, event=event
        ):
            raise ApprovalError(
                "APPROVAL_STATE_CONFLICT", "Approval state changed concurrently"
            )
        return updated


def _validated_context(action_context: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(action_context, Mapping):
        raise ApprovalError(
            "APPROVAL_CONTEXT_INVALID", "Action context must be an object"
        )
    context = dict(action_context)
    for field in (
        "organization_id",
        "requester_id",
        "target_approver_id",
        "action_id",
        "action_summary",
        "trace_id",
    ):
        try:
            _required_text(context.get(field), field)
        except ValueError as error:
            raise ApprovalError("APPROVAL_CONTEXT_INVALID", str(error)) from error
    if not isinstance(context.get("resource"), Mapping):
        raise ApprovalError("APPROVAL_CONTEXT_INVALID", "resource must be an object")
    risk = parse_risk_level(context.get("risk"))
    if risk is None:
        raise ApprovalError("APPROVAL_CONTEXT_INVALID", "risk is unknown")
    resource_organization = context["resource"].get("organization_id")
    if (
        resource_organization is not None
        and resource_organization != context["organization_id"]
    ):
        raise ApprovalError(
            "APPROVAL_CONTEXT_INVALID", "resource organization does not match action"
        )
    action = context.get("action")
    action_organization = (
        action.get("organization_id") if isinstance(action, Mapping) else None
    )
    if (
        action_organization is not None
        and action_organization != context["organization_id"]
    ):
        raise ApprovalError(
            "APPROVAL_CONTEXT_INVALID", "action organization does not match action"
        )
    context["risk"] = risk.value
    try:
        action_context_digest(context)
    except (TypeError, ValueError) as error:
        raise ApprovalError(
            "APPROVAL_CONTEXT_INVALID", "Action context is not JSON-safe"
        ) from error
    return context


def _validated_policy_decision(
    decision: PolicyDecision, context: Mapping[str, object]
) -> PolicyDecision:
    if not isinstance(decision, PolicyDecision) or not isinstance(
        decision.decision, PolicyOutcome
    ):
        raise ApprovalError(
            "POLICY_DECISION_INVALID", "A valid PolicyDecision is required"
        )
    risk = parse_risk_level(context.get("risk"))
    if (
        risk is None
        or decision.risk is not risk
        or decision.trace_id != context.get("trace_id")
        or not isinstance(decision.policy_version, str)
        or not decision.policy_version.strip()
        or not isinstance(decision.reason, str)
        or not decision.reason.strip()
        or (decision.rule_id is not None and not isinstance(decision.rule_id, str))
    ):
        raise ApprovalError(
            "POLICY_DECISION_INVALID", "Policy decision does not match action context"
        )
    return decision


def _safe_resource(resource: Mapping[str, object]) -> dict[str, object]:
    allowed = {
        "organization_id",
        "resource_type",
        "resource_id",
        "scope",
        "classification",
    }
    return {key: value for key, value in resource.items() if key in allowed}


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _required_text(value, "run_id")


def _aware_utc(value: datetime, field: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ApprovalError("APPROVAL_CLOCK_INVALID", f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _status_error(status: ApprovalStatus) -> ApprovalError:
    if status is ApprovalStatus.EXPIRED:
        return ApprovalError("APPROVAL_EXPIRED", "Approval request has expired")
    if status is ApprovalStatus.REJECTED:
        return ApprovalError("APPROVAL_REJECTED", "Rejected action cannot be retried")
    if status is ApprovalStatus.INVALIDATED:
        return ApprovalError("APPROVAL_CONTEXT_CHANGED", "Action context changed")
    return ApprovalError("APPROVAL_NOT_CONSUMABLE", "Approval is not consumable")
