from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import Engine, insert

from app.persistence.postgres import events

VERIFIED = "VERIFIED"
DENIED = "DENIED"


@dataclass(frozen=True, slots=True)
class KnowledgeAuditRecord:
    """Append-only audit metadata. Never carries query text, chunk content, or secrets."""

    organization_id: str
    subject_id: str
    trace_id: str
    outcome: str
    policy_version: str
    document_refs: tuple[Mapping[str, str], ...] = ()
    reason: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_payload(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "organization_id": self.organization_id,
            "trace_id": self.trace_id,
            "outcome": self.outcome,
            "policy_version": self.policy_version,
            "reason": self.reason,
            "document_refs": [dict(ref) for ref in self.document_refs],
            "occurred_at": self.occurred_at.isoformat(),
        }


class KnowledgeAuditSink(Protocol):
    def append(self, record: KnowledgeAuditRecord) -> None: ...


class InMemoryKnowledgeAuditSink:
    """Test/short-lived-process sink. Records are append-only."""

    def __init__(self) -> None:
        self.records: list[KnowledgeAuditRecord] = []

    def append(self, record: KnowledgeAuditRecord) -> None:
        self.records.append(record)


class PostgresKnowledgeAuditSink:
    """Durable audit sink backed by the Cloud `events` table."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(self, record: KnowledgeAuditRecord) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(events).values(
                    event_id=str(uuid4()),
                    organization_id=record.organization_id,
                    idempotency_key=(
                        f"knowledge-audit:{record.trace_id}:{record.outcome}"
                    ),
                    payload=record.to_payload(),
                    received_at=record.occurred_at,
                )
            )
