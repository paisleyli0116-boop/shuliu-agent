from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.knowledge.ingest import (
    KnowledgeChunkInput,
    KnowledgeDocumentInput,
    delete_document,
    ingest_document,
    replace_document_permissions,
)
from app.knowledge.embedding import EmbeddingProfile
from app.knowledge.retrieval import KnowledgeAuthorizationContext, KnowledgeRetriever
from app.persistence.postgres import create_postgres_engine


SERVICE_ROOT = Path(__file__).resolve().parents[2]


class FakeEmbedding:
    def __init__(self) -> None:
        self.profile = EmbeddingProfile(
            "test-embedding-v1",
            "fake-embedding",
            "fake/embed-v1",
            2,
            frozenset({"INTERNAL"}),
            1,
        )
        self.requests: list[dict[str, object]] = []

    def embed(self, texts, **context):
        self.requests.append({"capability": "embedding", "input": list(texts), **context})
        return tuple((0.25, 0.75) for _ in texts)


class RecordingInvalidator:
    def __init__(self) -> None:
        self.invalidations: list[tuple[str, str]] = []

    def invalidate_document(self, organization_id: str, document_id: str) -> None:
        self.invalidations.append((organization_id, document_id))


def _document(organization_id: str, document_id: str) -> KnowledgeDocumentInput:
    return KnowledgeDocumentInput(
        document_id=document_id,
        organization_id=organization_id,
        scope="team:finance",
        classification="INTERNAL",
        metadata={"source": "test"},
        chunks=(KnowledgeChunkInput("invoice totals", {"page": 1}),),
        permission_codes=("knowledge.read",),
        subject_id="user-a",
        permissions=("foundation.model.invoke", "knowledge.read"),
        privacy="INTERNAL",
        budget_ref=f"budget://{organization_id}/knowledge",
        trace_id="trace-ingest-1",
    )


def test_knowledge_storage_rejects_local_sqlite_vector_index() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    gateway = FakeEmbedding()
    invalidator = RecordingInvalidator()
    try:
        with pytest.raises(ValueError, match="Cloud PostgreSQL"):
            KnowledgeRetriever(engine, gateway)
        with pytest.raises(ValueError, match="Cloud PostgreSQL"):
            ingest_document(engine, _document("org-a", "doc-local"), gateway, invalidator)
        assert gateway.requests == []
        assert invalidator.invalidations == []
    finally:
        engine.dispose()


@pytest.mark.integration
def test_acl_revocation_and_document_delete_invalidate_cached_evidence() -> None:
    database_url = os.environ.get("SHULIU_DATABASE_URL")
    if not database_url:
        pytest.skip("SHULIU_DATABASE_URL is not configured")
    engine = create_postgres_engine(database_url)
    organization_id = f"org-revoke-{uuid4()}"
    document_id = f"doc-revoke-{uuid4()}"
    gateway = FakeEmbedding()
    invalidator = RecordingInvalidator()
    config = Config(str(SERVICE_ROOT / "alembic.ini"))
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

        ingest_document(engine, _document(organization_id, document_id), gateway, invalidator)
        retriever = KnowledgeRetriever(engine, gateway)
        context = KnowledgeAuthorizationContext(
            organization_id=organization_id,
            subject_id="user-a",
            scopes=("team:finance",),
            classifications=("INTERNAL",),
            permissions=("foundation.model.invoke",),
            knowledge_permissions=("knowledge.read",),
            privacy="INTERNAL",
            budget_ref=f"budget://{organization_id}/knowledge",
            trace_id="trace-revoke-read",
        )
        assert len(retriever.retrieve("invoice", context, 5)) == 1

        replace_document_permissions(engine, organization_id, document_id, (), invalidator)
        assert invalidator.invalidations[-1] == (organization_id, document_id)
        assert retriever.retrieve("invoice", context, 5) == []
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT COUNT(*) FROM knowledge_document_acl WHERE document_id = :document_id"),
                {"document_id": document_id},
            ).scalar_one() == 0

        delete_document(engine, organization_id, document_id, invalidator)
        assert invalidator.invalidations[-1] == (organization_id, document_id)
        assert retriever.retrieve("invoice", context, 5) == []
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT COUNT(*) FROM knowledge_documents WHERE document_id = :document_id"),
                {"document_id": document_id},
            ).scalar_one() == 0
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM knowledge_documents WHERE organization_id = :organization_id"),
                {"organization_id": organization_id},
            )
        engine.dispose()
