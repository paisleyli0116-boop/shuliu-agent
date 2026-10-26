from __future__ import annotations

import pytest
from app.knowledge.acl import KnowledgeAuthorizationContext
from app.knowledge.audit import DENIED, VERIFIED, InMemoryKnowledgeAuditSink
from app.knowledge.dependencies import (
    MembershipSnapshot,
    build_knowledge_context,
)
from app.knowledge.retrieval import KnowledgeHit
from app.knowledge.routes import create_knowledge_router
from app.identity.api_auth import current_user_dependency
from fastapi import FastAPI
from fastapi.testclient import TestClient

USER_ID = "user-1"
ORG_ID = "org-a"
POLICY_VERSION = "knowledge-policy-v1"


class _StubMembershipPort:
    def __init__(self, snapshot: MembershipSnapshot | None) -> None:
        self.snapshot = snapshot

    def current_membership(self, user_id: str) -> MembershipSnapshot | None:
        return self.snapshot


class _StubRBAC:
    def __init__(self, *, result: bool = True, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, object]] = []

    def authorize(self, **kwargs: object) -> bool:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class _StubRetriever:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def retrieve(
        self,
        query: str,
        context: KnowledgeAuthorizationContext,
        top_k: int,
    ) -> list[KnowledgeHit]:
        self.calls.append({"query": query, "context": context, "top_k": top_k})
        return [
            KnowledgeHit(
                chunk_id="chunk-1",
                document_id="doc-1",
                content="approved knowledge body",
                source_reference={"uri": "doc-1#chunk-1"},
                distance=0.12,
            )
        ]


class _ExplodingAuditSink:
    def append(self, record: object) -> None:
        raise RuntimeError("audit storage down")


def _snapshot(organization_id: str = ORG_ID) -> MembershipSnapshot:
    return MembershipSnapshot(
        organization_id=organization_id,
        membership_id="membership-1",
        status="ACTIVE",
        scopes=("team:finance",),
        classifications=("INTERNAL",),
        permissions=("foundation.model.invoke",),
        knowledge_permissions=("knowledge.read",),
        privacy="INTERNAL",
        budget_ref=f"budget://{organization_id}/knowledge",
    )


def _client(
    *,
    membership: _StubMembershipPort | None = None,
    rbac: _StubRBAC | None = None,
    retriever: _StubRetriever | None = None,
    audit_sink: object | None = None,
    current_user: object | None = None,
):
    app = FastAPI()
    app.include_router(
        create_knowledge_router(
            membership_port=membership or _StubMembershipPort(_snapshot()),
            rbac_port=rbac or _StubRBAC(),
            retriever=retriever or _StubRetriever(),
            audit_sink=audit_sink or InMemoryKnowledgeAuditSink(),
            policy_version=POLICY_VERSION,
            current_user=current_user or (lambda: USER_ID),
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def _search_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {"query": "how to file quarterly tax", "top_k": 3}
    body.update(overrides)
    return body


def test_forged_authorization_fields_are_rejected() -> None:
    retriever = _StubRetriever()
    client = _client(retriever=retriever)
    forged = _search_body(
        organization_id="org-evil",
        subject_id="attacker",
        scopes=["team:all"],
        classifications=["PUBLIC"],
        permissions=["knowledge.admin"],
        knowledge_permissions=["knowledge.read", "knowledge.export"],
        budget_ref="budget://org-evil/knowledge",
        privacy="PUBLIC",
    )
    response = client.post("/v1/knowledge/search", json=forged)
    assert response.status_code == 422
    assert retriever.calls == []


def test_server_computed_context_is_used_for_retrieval() -> None:
    rbac = _StubRBAC()
    retriever = _StubRetriever()
    client = _client(rbac=rbac, retriever=retriever)
    response = client.post("/v1/knowledge/search", json=_search_body())
    assert response.status_code == 200
    assert len(rbac.calls) == 1
    assert rbac.calls[0]["user_id"] == USER_ID
    assert rbac.calls[0]["organization_id"] == ORG_ID
    assert rbac.calls[0]["permission"] == "knowledge.read"
    context = retriever.calls[0]["context"]
    assert isinstance(context, KnowledgeAuthorizationContext)
    assert context.organization_id == ORG_ID
    assert context.subject_id == USER_ID
    assert context.scopes == ("team:finance",)
    assert context.classifications == ("INTERNAL",)
    assert context.budget_ref == "budget://org-a/knowledge"
    assert retriever.calls[0]["top_k"] == 3


def test_missing_membership_is_denied_and_audited() -> None:
    audit_sink = InMemoryKnowledgeAuditSink()
    retriever = _StubRetriever()
    client = _client(
        membership=_StubMembershipPort(None),
        retriever=retriever,
        audit_sink=audit_sink,
    )
    response = client.post("/v1/knowledge/search", json=_search_body())
    assert response.status_code == 403
    assert retriever.calls == []
    assert len(audit_sink.records) == 1
    record = audit_sink.records[0]
    assert record.outcome == DENIED
    assert record.subject_id == USER_ID
    assert record.trace_id == response.json()["detail"]["trace_id"]
    assert "how to file quarterly tax" not in str(record.to_payload())


def test_revoked_permission_is_denied_and_audited() -> None:
    audit_sink = InMemoryKnowledgeAuditSink()
    rbac = _StubRBAC(result=False)
    retriever = _StubRetriever()
    client = _client(
        rbac=rbac, retriever=retriever, audit_sink=audit_sink
    )
    response = client.post("/v1/knowledge/search", json=_search_body())
    assert response.status_code == 403
    assert retriever.calls == []
    record = audit_sink.records[0]
    assert record.outcome == DENIED
    assert record.organization_id == ORG_ID
    assert record.policy_version == POLICY_VERSION


def test_rbac_service_failure_fails_closed_without_leaking_errors() -> None:
    audit_sink = InMemoryKnowledgeAuditSink()
    rbac = _StubRBAC(error=RuntimeError("rbac postgres connection refused"))
    retriever = _StubRetriever()
    client = _client(
        rbac=rbac, retriever=retriever, audit_sink=audit_sink
    )
    response = client.post("/v1/knowledge/search", json=_search_body())
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "KNOWLEDGE_AUTHORIZATION_DENIED"
    assert "postgres" not in response.text
    assert "connection refused" not in response.text
    assert retriever.calls == []
    assert audit_sink.records[0].outcome == DENIED


def test_unauthenticated_request_is_rejected_without_audit() -> None:
    audit_sink = InMemoryKnowledgeAuditSink()
    retriever = _StubRetriever()
    client = _client(
        retriever=retriever,
        audit_sink=audit_sink,
        current_user=current_user_dependency(None),
    )
    response = client.post("/v1/knowledge/search", json=_search_body())
    assert response.status_code == 401
    assert retriever.calls == []
    assert audit_sink.records == []


def test_happy_path_returns_hits_and_verified_audit() -> None:
    audit_sink = InMemoryKnowledgeAuditSink()
    retriever = _StubRetriever()
    client = _client(retriever=retriever, audit_sink=audit_sink)
    response = client.post("/v1/knowledge/search", json=_search_body())
    assert response.status_code == 200
    body = response.json()
    assert body["policy_version"] == POLICY_VERSION
    assert [hit["document_id"] for hit in body["hits"]] == ["doc-1"]
    assert body["hits"][0]["content"] == "approved knowledge body"
    for forbidden in ("organization_id", "permissions", "scopes", "budget_ref", "privacy"):
        assert forbidden not in body
    record = audit_sink.records[0]
    assert record.outcome == VERIFIED
    assert record.trace_id == body["trace_id"]
    assert record.subject_id == USER_ID
    assert record.organization_id == ORG_ID
    assert record.policy_version == POLICY_VERSION
    refs = [dict(ref) for ref in record.document_refs]
    assert {"document_id": "doc-1", "chunk_id": "chunk-1"} in refs


def test_context_builder_rejects_inactive_membership_directly() -> None:
    from app.knowledge.dependencies import KnowledgeAuthorizationDenied

    with pytest.raises(KnowledgeAuthorizationDenied):
        build_knowledge_context(
            USER_ID,
            None,
            _StubRBAC(),
            trace_id="trace-1",
        )
    revoked = MembershipSnapshot(
        organization_id=ORG_ID,
        membership_id="membership-1",
        status="REVOKED",
        scopes=("team:finance",),
        classifications=("INTERNAL",),
        permissions=("foundation.model.invoke",),
        knowledge_permissions=("knowledge.read",),
        privacy="INTERNAL",
        budget_ref="budget://org-a/knowledge",
    )
    with pytest.raises(KnowledgeAuthorizationDenied):
        build_knowledge_context(
            USER_ID,
            revoked,
            _StubRBAC(),
            trace_id="trace-2",
        )
