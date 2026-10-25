from __future__ import annotations

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

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


class FakeEmbedding:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors
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
        request = {
            "capability": "embedding",
            "input": list(texts),
            **context,
        }
        self.requests.append(request)
        return tuple(tuple(self.vectors[text]) for text in texts)


class RecordingInvalidator:
    def invalidate_document(self, organization_id: str, document_id: str) -> None:
        return None


def _context(organization_id: str) -> KnowledgeAuthorizationContext:
    return KnowledgeAuthorizationContext(
        organization_id=organization_id,
        subject_id="user-reader",
        scopes=("team:finance",),
        classifications=("INTERNAL",),
        permissions=("foundation.model.invoke",),
        knowledge_permissions=("knowledge.read",),
        privacy="INTERNAL",
        budget_ref=f"budget://{organization_id}/knowledge",
        trace_id="trace-knowledge-integration",
    )


@pytest.mark.integration
def test_unauthorized_nearest_chunk_is_filtered_before_vector_search_and_revocation_is_immediate():
    database_url = os.environ.get("SHULIU_DATABASE_URL")
    if not database_url:
        pytest.skip("SHULIU_DATABASE_URL is not configured")
    engine = create_postgres_engine(database_url)
    organization_id = f"org-knowledge-{uuid4()}"
    authorized_id = f"doc-authorized-{uuid4()}"
    restricted_id = f"doc-restricted-{uuid4()}"
    gateway = FakeEmbedding({"query": [0.0, 0.0], "authorized": [1.0, 0.0], "restricted": [0.0, 0.0]})
    invalidator = RecordingInvalidator()
    config = Config("services/cloud-api/alembic.ini")
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

        for document_id, content, permission in (
            (authorized_id, "authorized", "knowledge.read"),
            (restricted_id, "restricted", "knowledge.private"),
        ):
            ingest_document(
                engine,
                KnowledgeDocumentInput(
                    document_id=document_id,
                    organization_id=organization_id,
                    scope="team:finance",
                    classification="INTERNAL",
                    metadata={"source": "integration-test"},
                    chunks=(KnowledgeChunkInput(content, {"document_id": document_id}),),
                    permission_codes=(permission,),
                    subject_id="user-admin",
                    permissions=("foundation.model.invoke", "knowledge.read"),
                    privacy="INTERNAL",
                    budget_ref=f"budget://{organization_id}/knowledge",
                    trace_id=f"trace-{document_id}",
                ),
                gateway,
                invalidator,
            )

        retriever = KnowledgeRetriever(engine, gateway)
        hits = retriever.retrieve("query", _context(organization_id), 5)
        assert [hit.document_id for hit in hits] == [authorized_id]
        assert all(request["capability"] == "embedding" for request in gateway.requests)
        assert all(request["budget_ref"] == f"budget://{organization_id}/knowledge" for request in gateway.requests)
        assert all(request["privacy"] == "INTERNAL" for request in gateway.requests)
        with engine.connect() as connection:
            profile_row = connection.execute(
                text(
                    "SELECT embedding_profile_id, embedding_provider_id, embedding_model, "
                    "embedding_dimensions, embedding_index_version FROM knowledge_chunks "
                    "WHERE organization_id = :organization_id AND document_id = :document_id"
                ),
                {"organization_id": organization_id, "document_id": authorized_id},
            ).one()
        assert tuple(profile_row) == (
            "test-embedding-v1", "fake-embedding", "fake/embed-v1", 2, 1
        )

        alternate_profile = FakeEmbedding(gateway.vectors)
        alternate_profile.profile = EmbeddingProfile(
            "test-embedding-v2", "fake-embedding", "fake/embed-v2", 2,
            frozenset({"INTERNAL"}), 2,
        )
        assert KnowledgeRetriever(engine, alternate_profile).retrieve(
            "query", _context(organization_id), 5
        ) == []

        replace_document_permissions(engine, organization_id, authorized_id, ("knowledge.private",), invalidator)
        assert retriever.retrieve("query", _context(organization_id), 5) == []
        delete_document(engine, organization_id, authorized_id, invalidator)
        assert retriever.retrieve("query", _context(organization_id), 5) == []
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM knowledge_documents WHERE organization_id = :organization_id"),
                {"organization_id": organization_id},
            )
        engine.dispose()
