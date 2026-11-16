from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from shuliu_runtime.approval.service import (
    Approval,
    ApprovalAuditEvent,
    ApprovalStatus,
)
from shuliu_runtime.events.observability import redact
from shuliu_runtime.persistence.outbox import canonical_json
from shuliu_runtime.persistence.sqlite import IdempotencyConflictError, SQLiteDatabase
from shuliu_runtime.policy.risk import RiskLevel, parse_risk_level

_COLUMNS = (
    "approval_id",
    "organization_id",
    "requester_id",
    "target_approver_id",
    "action_id",
    "action_summary",
    "resource_json",
    "risk",
    "action_digest",
    "policy_decision_json",
    "trace_id",
    "run_id",
    "created_at",
    "expires_at",
    "status",
    "decision_by",
    "decision_at",
)

_INSERT = (
    f"INSERT INTO approval_requests ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _COLUMNS)})"
)


class SQLiteApprovalRepository:
    """Durable Approval state with its audit trail committed in one transaction.

    The repository implements the `ApprovalRepository` protocol the Approval
    Service expects: `transition` is a compare-and-set on the status it read, so
    two concurrent decisions cannot both win, and a refused transition writes no
    audit event.
    """

    def __init__(self, database: SQLiteDatabase) -> None:
        if not isinstance(database, SQLiteDatabase):
            raise TypeError("approval repository requires SQLiteDatabase")
        self._database = database

    def create(self, approval: Approval, event: ApprovalAuditEvent) -> None:
        if not isinstance(approval, Approval):
            raise TypeError("approval must be an Approval")
        if not isinstance(event, ApprovalAuditEvent):
            raise TypeError("event must be an ApprovalAuditEvent")
        with self._database.connect() as connection:
            try:
                connection.execute(_INSERT, _values(approval))
                self._append_event(connection, event)
            except sqlite3.IntegrityError as error:
                raise IdempotencyConflictError(
                    "approval or its audit event already exists"
                ) from error

    def get(
        self, approval_id: str, organization_id: str | None = None
    ) -> Approval | None:
        query = "SELECT * FROM approval_requests WHERE approval_id = ?"
        parameters: list[object] = [approval_id]
        if organization_id is not None:
            query += " AND organization_id = ?"
            parameters.append(organization_id)
        with self._database.connect() as connection:
            row = connection.execute(query, tuple(parameters)).fetchone()
        return None if row is None else _approval(row)

    def transition(
        self,
        approval: Approval,
        expected_status: ApprovalStatus,
        event: ApprovalAuditEvent,
    ) -> bool:
        if not isinstance(approval, Approval) or not isinstance(
            expected_status, ApprovalStatus
        ):
            raise TypeError("transition requires an Approval and a status")
        with self._database.connect() as connection:
            cursor = connection.execute(
                "UPDATE approval_requests SET status = ?, decision_by = ?, "
                "decision_at = ? WHERE approval_id = ? AND organization_id = ? "
                "AND status = ?",
                (
                    approval.status.value,
                    approval.decision_by,
                    _timestamp(approval.decision_at),
                    approval.approval_id,
                    approval.organization_id,
                    expected_status.value,
                ),
            )
            if cursor.rowcount != 1:
                return False
            self._append_event(connection, event)
            return True

    def _append_event(
        self, connection: sqlite3.Connection, event: ApprovalAuditEvent
    ) -> None:
        payload = redact(dict(event.payload))
        if not isinstance(payload, dict):
            raise ValueError("approval audit payload must be an object")
        occurred_at = _timestamp(event.timestamp)
        connection.execute(
            "INSERT INTO approval_audit_events (event_id, idempotency_key, "
            "event_type, organization_id, trace_id, occurred_at, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event.event_id,
                event.idempotency_key,
                event.event_type,
                event.organization_id,
                event.trace_id,
                occurred_at,
                canonical_json(payload),
            ),
        )
        # 同一事务内把审计事件放进 outbox，保证审批状态与待同步事件不会分叉。
        connection.execute(
            "INSERT INTO outbox (event_id, idempotency_key, payload_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                event.event_id,
                event.idempotency_key,
                canonical_json(
                    {
                        "event_id": event.event_id,
                        "timestamp": occurred_at,
                        "organization_id": event.organization_id,
                        "trace_id": event.trace_id,
                        "event_type": event.event_type,
                        "payload": payload,
                    }
                ),
                datetime.now(UTC).isoformat(),
            ),
        )


def _values(approval: Approval) -> tuple[object, ...]:
    return (
        approval.approval_id,
        approval.organization_id,
        approval.requester_id,
        approval.target_approver_id,
        approval.action_id,
        approval.action_summary,
        canonical_json(dict(approval.resource)),
        _risk(approval.risk).value,
        approval.action_digest,
        canonical_json(dict(approval.policy_decision)),
        approval.trace_id,
        approval.run_id,
        _timestamp(approval.created_at),
        _timestamp(approval.expires_at),
        approval.status.value,
        approval.decision_by,
        _timestamp(approval.decision_at),
    )


def _approval(row: sqlite3.Row) -> Approval:
    risk = parse_risk_level(row["risk"])
    if risk is None:
        raise ValueError("stored approval risk is unknown")
    return Approval(
        approval_id=row["approval_id"],
        organization_id=row["organization_id"],
        requester_id=row["requester_id"],
        target_approver_id=row["target_approver_id"],
        action_id=row["action_id"],
        action_summary=row["action_summary"],
        resource=json.loads(row["resource_json"]),
        risk=risk,
        action_digest=row["action_digest"],
        policy_decision=json.loads(row["policy_decision_json"]),
        trace_id=row["trace_id"],
        run_id=row["run_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]),
        status=ApprovalStatus(row["status"]),
        decision_by=row["decision_by"],
        decision_at=(
            datetime.fromisoformat(row["decision_at"])
            if row["decision_at"] is not None
            else None
        ),
    )


def _risk(value: object) -> RiskLevel:
    risk = parse_risk_level(value)
    if risk is None:
        raise ValueError("approval risk is unknown")
    return risk


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("approval timestamps must be timezone aware")
    return value.astimezone(UTC).isoformat()
