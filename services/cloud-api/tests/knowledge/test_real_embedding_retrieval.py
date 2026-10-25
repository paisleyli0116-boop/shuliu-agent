from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, text

from app.knowledge.embedding import EmbeddingIndexNotReadyError, EmbeddingProfile
from app.knowledge.ingest import KnowledgeChunkInput, KnowledgeDocumentInput, ingest_document
from app.knowledge.retrieval import KnowledgeAuthorizationContext, KnowledgeRetriever
from app.persistence.postgres import create_postgres_engine, knowledge_embedding_profiles


SERVICE_ROOT = Path(__file__).resolve().parents[2]


class FakeEmbedding:
    def __init__(self, profile: EmbeddingProfile) -> None:
        self.profile = profile
        self.calls: list[tuple[str, ...]] = []

    def embed(self, texts, **_context):
        self.calls.append(tuple(texts))
        return tuple((1.0, 0.0, 0.0) for _ in texts)


class NoopInvalidator:
    def invalidate_document(self, _organization_id: str, _document_id: str) -> None:
        return None


def _profile(version: int) -> EmbeddingProfile:
    return EmbeddingProfile(
        "knowledge-test",
        "fake-provider",
        f"fake/embed-v{version}",
        3,
        frozenset({"PUBLIC"}),
        version,
    )


def _document(organization_id: str, document_id: str) -> KnowledgeDocumentInput:
    return KnowledgeDocumentInput(
        document_id=document_id,
        organization_id=organization_id,
        scope="team:test",
        classification="PUBLIC",
        metadata={"source": "task30-integration"},
        chunks=(KnowledgeChunkInput(f"public knowledge {document_id}", {"source": document_id}),),
        permission_codes=("knowledge.read",),
        subject_id="task30-writer",
        permissions=("foundation.model.invoke", "knowledge.read"),
        privacy="PUBLIC",
        budget_ref=f"budget://{organization_id}/knowledge",
        trace_id=f"trace-ingest-{document_id}",
    )


def _context(organization_id: str) -> KnowledgeAuthorizationContext:
    return KnowledgeAuthorizationContext(
        organization_id=organization_id,
        subject_id="task30-reader",
        scopes=("team:test",),
        classifications=("PUBLIC",),
        permissions=("foundation.model.invoke", "knowledge.read"),
        knowledge_permissions=("knowledge.read",),
        privacy="PUBLIC",
        budget_ref=f"budget://{organization_id}/knowledge",
        trace_id="trace-task30-query",
    )


@pytest.mark.integration
def test_profile_reindex_blocks_partial_search_until_every_document_is_reembedded():
    database_url = os.environ.get("SHULIU_DATABASE_URL")
    if not database_url:
        pytest.skip("SHULIU_DATABASE_URL is not configured")
    engine = create_postgres_engine(database_url)
    organization_id = f"task30-{uuid4().hex}"
    document_ids = (f"doc-{uuid4().hex}", f"doc-{uuid4().hex}")
    invalidator = NoopInvalidator()
    try:
        with engine.begin() as connection:
            config = Config(str(SERVICE_ROOT / "alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

        v1_embedder = FakeEmbedding(_profile(1))
        for document_id in document_ids:
            ingest_document(engine, _document(organization_id, document_id), v1_embedder, invalidator)
        assert len(KnowledgeRetriever(engine, v1_embedder).retrieve("query", _context(organization_id), 5)) == 2

        v2_embedder = FakeEmbedding(_profile(2))
        ingest_document(engine, _document(organization_id, document_ids[0]), v2_embedder, invalidator)
        calls_before_blocked_query = len(v2_embedder.calls)
        with pytest.raises(EmbeddingIndexNotReadyError):
            KnowledgeRetriever(engine, v2_embedder).retrieve("query", _context(organization_id), 5)
        assert len(v2_embedder.calls) == calls_before_blocked_query

        with engine.connect() as connection:
            status = connection.execute(
                text(
                    "SELECT state FROM knowledge_embedding_profiles "
                    "WHERE organization_id = :organization_id AND profile_id = :profile_id "
                    "AND index_version = :index_version"
                ),
                {"organization_id": organization_id, "profile_id": "knowledge-test", "index_version": 2},
            ).scalar_one()
        assert status == "BUILDING"

        ingest_document(engine, _document(organization_id, document_ids[1]), v2_embedder, invalidator)
        hits = KnowledgeRetriever(engine, v2_embedder).retrieve("query", _context(organization_id), 5)
        assert {hit.document_id for hit in hits} == set(document_ids)
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM knowledge_documents WHERE organization_id = :organization_id"),
                {"organization_id": organization_id},
            )
            connection.execute(
                delete(knowledge_embedding_profiles).where(
                    knowledge_embedding_profiles.c.organization_id == organization_id
                )
            )
        engine.dispose()
