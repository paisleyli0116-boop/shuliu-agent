from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Literal

from app.persistence.postgres import approvals, events
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from shuliu_runtime.approval.service import (
    Approval,
    ApprovalAuditEvent,
    ApprovalError,
    ApprovalService,
    ApprovalStatus,
)
from shuliu_runtime.policy.risk import RiskLevel
from sqlalchemy import Engine, insert, select, update


class DecisionBody(BaseModel):
    decision: Literal["APPROVE", "REJECT"]


class PostgresApprovalRepository:
    """Stores approval state and append-only audit events in Cloud PostgreSQL."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, approval: Approval, event: ApprovalAuditEvent) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(approvals).values(
                    approval_id=approval.approval_id,
                    organization_id=approval.organization_id,
                    run_id=approval.run_id,
                    status=approval.status.value,
                    action_digest=approval.action_digest,
                    created_at=approval.created_at,
                )
            )
            self._append_event(connection, event)

    def get(
        self, approval_id: str, organization_id: str | None = None
    ) -> Approval | None:
        with self._engine.connect() as connection:
            query = select(approvals).where(approvals.c.approval_id == approval_id)
            if organization_id is not None:
                query = query.where(approvals.c.organization_id == organization_id)
            saved = connection.execute(query).mappings().first()
            if saved is None:
                return None
            request = connection.execute(
                select(events.c.payload).where(
                    events.c.organization_id == saved["organization_id"],
                    events.c.idempotency_key
                    == f"approval:{approval_id}:approval_requested",
                )
            ).scalar_one_or_none()
            if not isinstance(request, Mapping):
                raise TypeError(
                    "persisted approval request event is missing or invalid"
                )
            request_payload = request.get("payload")
            if not isinstance(request_payload, Mapping):
                raise TypeError("persisted approval event payload is invalid")
            latest = (
                connection.execute(
                    select(events.c.payload)
                    .where(
                        events.c.organization_id == saved["organization_id"],
                        events.c.idempotency_key.like(f"approval:{approval_id}:%"),
                    )
                    .order_by(events.c.received_at.desc(), events.c.event_id.desc())
                )
                .mappings()
                .all()
            )
            decision_event = next(
                (
                    record["payload"]
                    for record in latest
                    if isinstance(record["payload"], Mapping)
                    and record["payload"].get("event_type")
                    in ("APPROVAL_APPROVED", "APPROVAL_REJECTED")
                ),
                None,
            )
            decision_payload = (
                decision_event.get("payload")
                if isinstance(decision_event, Mapping)
                else {}
            )
            if not isinstance(decision_payload, Mapping):
                decision_payload = {}
            return Approval(
                approval_id=approval_id,
                organization_id=saved["organization_id"],
                requester_id=request_payload["requester_id"],
                target_approver_id=request_payload["target_approver_id"],
                action_id=request_payload["action_id"],
                action_summary=request_payload["action_summary"],
                resource=request_payload["resource"],
                risk=RiskLevel(request_payload["risk"]),
                action_digest=saved["action_digest"],
                policy_decision=request_payload["policy_decision"],
                trace_id=request["trace_id"],
                run_id=saved["run_id"],
                created_at=_aware(saved["created_at"]),
                expires_at=_parse_timestamp(request_payload["expires_at"]),
                status=ApprovalStatus(saved["status"]),
                decision_by=decision_payload.get("decision_by"),
                decision_at=(
                    _parse_timestamp(decision_event["timestamp"])
                    if isinstance(decision_event, Mapping)
                    else None
                ),
            )

    def transition(
        self,
        approval: Approval,
        expected_status: ApprovalStatus,
        event: ApprovalAuditEvent,
    ) -> bool:
        with self._engine.begin() as connection:
            result = connection.execute(
                update(approvals)
                .where(
                    approvals.c.approval_id == approval.approval_id,
                    approvals.c.organization_id == approval.organization_id,
                    approvals.c.status == expected_status.value,
                )
                .values(status=approval.status.value)
            )
            if result.rowcount != 1:
                return False
            self._append_event(connection, event)
            return True

    @staticmethod
    def _append_event(connection, event: ApprovalAuditEvent) -> None:
        connection.execute(
            insert(events).values(
                event_id=event.event_id,
                organization_id=event.organization_id,
                idempotency_key=event.idempotency_key,
                payload={
                    "schema_version": 1,
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "timestamp": event.timestamp.isoformat(),
                    "organization_id": event.organization_id,
                    "trace_id": event.trace_id,
                    "payload": dict(event.payload),
                },
                received_at=event.timestamp,
            )
        )


def create_approval_router(
    service: ApprovalService,
    current_user: Callable[[], str],
) -> APIRouter:
    router = APIRouter(prefix="/approvals", tags=["approvals"])

    @router.post("/{approval_id}/decision")
    def decide_approval(
        approval_id: str,
        body: DecisionBody,
        approver_id: str = Depends(current_user),
    ) -> dict[str, object]:
        try:
            approval = service.decide(
                approval_id,
                approver_id=approver_id,
                decision=body.decision,
            )
        except ApprovalError as error:
            if error.code in ("APPROVAL_NOT_AUTHORIZED", "APPROVAL_SELF_APPROVAL"):
                status_code = 403
            elif error.code == "APPROVAL_NOT_FOUND":
                status_code = 404
            elif error.code in ("APPROVAL_DECISION_INVALID",):
                status_code = 422
            else:
                status_code = 409
            raise HTTPException(
                status_code=status_code, detail={"code": error.code}
            ) from error
        return _approval_response(approval)

    return router


def _approval_response(approval: Approval) -> dict[str, object]:
    return {
        "approval_id": approval.approval_id,
        "organization_id": approval.organization_id,
        "status": approval.status.value,
        "risk": approval.risk.value,
        "expires_at": approval.expires_at.isoformat(),
        "decision_by": approval.decision_by,
        "decision_at": approval.decision_at.isoformat()
        if approval.decision_at
        else None,
    }


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("persisted approval timestamp must be a string")
    try:
        return _aware(datetime.fromisoformat(value))
    except ValueError as error:
        raise ValueError("persisted approval timestamp is invalid") from error
