from __future__ import annotations

import json
from datetime import UTC, datetime

from app.knowledge.audit import (
    DENIED,
    VERIFIED,
    InMemoryKnowledgeAuditSink,
    KnowledgeAuditRecord,
    PostgresKnowledgeAuditSink,
)
from app.knowledge.dependencies import MembershipSnapshot
from app.knowledge.routes import create_knowledge_router
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from app.persistence.postgres import events, metadata

TRACE_ID = "trace-audit-1"
POLICY_VERSION = "knowledge-policy-v1"


class _StubMembershipPort:
    def __init__(self, snapshot: MembershipSnapshot) -> None:
        self.snapshot = snapshot

    def current_membership(self, user_id: str) -> MembershipSnapshot | None:
        return self.snapshot


class _StubRBAC:
    def authorize(self, **kwargs: object) -> bool:
        return True


class _StubRetriever:
    def retrieve(self, query: str, context: object, top_k: int) -> list[dict[str, object]]:
        return [
            {
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "content": "retrieved secret body text",
                "source_reference": {"uri": "doc-1#chunk-1"},
                "distance": 0.1,
            }
        ]


class _ExplodingRetriever:
    def retrieve(self, query: str, context: object, top_k: int) -> list[object]:
        raise RuntimeError("embedding index not ready")


class _ExplodingAuditSink:
    def append(self, record: object) -> None:
        raise RuntimeError("audit storage down")


def _record(**overrides: object) -> KnowledgeAuditRecord:
    values: dict[str, object] = {
        "organization_id": "org-a",
        "subject_id": "user-1",
        "trace_id": TRACE_ID,
        "outcome": VERIFIED,
        "policy_version": POLICY_VERSION,
        "document_refs": ({"document_id": "doc-1", "chunk_id": "chunk-1"},),
        "occurred_at": datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return KnowledgeAuditRecord(**values)  # type: ignore[arg-type]


def _snapshot() -> MembershipSnapshot:
    return MembershipSnapshot(
        organization_id="org-a",
        membership_id="membership-1",
        status="ACTIVE",
        scopes=("team:finance",),
        classifications=("INTERNAL",),
        permissions=("foundation.model.invoke",),
        knowledge_permissions=("knowledge.read",),
        privacy="INTERNAL",
        budget_ref="budget://org-a/knowledge",
    )


def _client(*, retriever: object, audit_sink: object) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_knowledge_router(
            membership_port=_StubMembershipPort(_snapshot()),
            rbac_port=_StubRBAC(),
            retriever=retriever,
            audit_sink=audit_sink,
            policy_version=POLICY_VERSION,
            current_user=lambda: "user-1",
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def test_audit_payload_holds_metadata_without_sensitive_text() -> None:
    record = _record()
    payload = record.to_payload()
    serialized = json.dumps(payload, default=str)
    assert payload["trace_id"] == TRACE_ID
    assert payload["subject_id"] == "user-1"
    assert payload["organization_id"] == "org-a"
    assert payload["outcome"] == VERIFIED
    assert payload["policy_version"] == POLICY_VERSION
    assert {"document_id": "doc-1", "chunk_id": "chunk-1"} in payload["document_refs"]
    assert "occurred_at" in payload
    assert "query" not in payload
    assert "content" not in payload
    assert "secret body" not in serialized


def test_postgres_audit_sink_appends_into_events() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    sink = PostgresKnowledgeAuditSink(engine)
    sink.append(_record())
    with engine.connect() as connection:
        rows = connection.execute(select(events.c.payload, events.c.idempotency_key)).all()
    assert len(rows) == 1
    payload, idempotency_key = rows[0]
    assert idempotency_key == f"knowledge-audit:{TRACE_ID}:{VERIFIED}"
    assert payload["trace_id"] == TRACE_ID
    assert payload["outcome"] == VERIFIED


def test_audit_append_failure_fails_closed_without_hits() -> None:
    retriever = _StubRetriever()
    client = _client(retriever=retriever, audit_sink=_ExplodingAuditSink())
    response = client.post(
        "/v1/knowledge/search", json={"query": "anything", "top_k": 3}
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "KNOWLEDGE_AUDIT_UNAVAILABLE"
    assert "hits" not in response.json()
    assert "retrieved secret body text" not in response.text


def test_retrieval_failure_fails_closed_without_content_or_verified_audit() -> None:
    audit_sink = InMemoryKnowledgeAuditSink()
    client = _client(retriever=_ExplodingRetriever(), audit_sink=audit_sink)
    response = client.post(
        "/v1/knowledge/search", json={"query": "anything", "top_k": 3}
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "KNOWLEDGE_RETRIEVAL_UNAVAILABLE"
    assert "not ready" not in response.text
    assert all(record.outcome != VERIFIED for record in audit_sink.records)
    assert all(record.outcome != DENIED for record in audit_sink.records)
