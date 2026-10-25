from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import and_, func, select, text
from sqlalchemy.engine import Engine

from .acl import (
    KnowledgeAuthorizationContext,
    authorization_allows_retrieval,
    authorized_document_predicate,
)
from .embedding import (
    EmbeddingIndexNotReadyError,
    EmbeddingPort,
    EmbeddingProfile,
    _validated_vector,
)
from app.persistence.postgres import (
    knowledge_chunks,
    knowledge_documents,
    knowledge_embedding_profiles,
)


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    chunk_id: str
    document_id: str
    content: str
    source_reference: Mapping[str, object]
    distance: float


def build_retrieval_statement(
    authorization_context: KnowledgeAuthorizationContext,
    profile: EmbeddingProfile,
    query_embedding: Sequence[float],
    top_k: int,
):
    if not authorization_allows_retrieval(authorization_context):
        raise ValueError("knowledge authorization context is incomplete")
    if not isinstance(profile, EmbeddingProfile):
        raise ValueError("knowledge embedding profile is required")
    validated = _validated_vector(query_embedding, profile.dimensions)
    vector = "[" + ",".join(format(item, ".17g") for item in validated) + "]"
    limit = _validated_top_k(top_k)
    return text(
        f"""
        WITH authorized_chunks AS MATERIALIZED (
            SELECT knowledge_chunks.chunk_id,
                   knowledge_chunks.document_id,
                   knowledge_chunks.content,
                   knowledge_chunks.source_reference,
                   knowledge_chunks.embedding,
                   knowledge_chunks.embedding_profile_id,
                   knowledge_chunks.embedding_provider_id,
                   knowledge_chunks.embedding_model,
                   knowledge_chunks.embedding_dimensions,
                   knowledge_chunks.embedding_index_version
            FROM knowledge_chunks
            JOIN knowledge_documents
              ON knowledge_documents.organization_id = knowledge_chunks.organization_id
             AND knowledge_documents.document_id = knowledge_chunks.document_id
            WHERE {authorized_document_predicate()}
              AND knowledge_chunks.embedding_profile_id = :embedding_profile_id
              AND knowledge_chunks.embedding_provider_id = :embedding_provider_id
              AND knowledge_chunks.embedding_model = :embedding_model
              AND knowledge_chunks.embedding_dimensions = :embedding_dimensions
              AND knowledge_chunks.embedding_index_version = :embedding_index_version
              AND EXISTS (
                  SELECT 1
                  FROM knowledge_embedding_profiles
                  WHERE knowledge_embedding_profiles.organization_id = :organization_id
                    AND knowledge_embedding_profiles.profile_id = :embedding_profile_id
                    AND knowledge_embedding_profiles.index_version = :embedding_index_version
                    AND knowledge_embedding_profiles.provider_id = :embedding_provider_id
                    AND knowledge_embedding_profiles.model = :embedding_model
                    AND knowledge_embedding_profiles.dimensions = :embedding_dimensions
                    AND knowledge_embedding_profiles.state = 'READY'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM knowledge_documents AS all_documents
                  WHERE all_documents.organization_id = :organization_id
                    AND NOT EXISTS (
                        SELECT 1
                        FROM knowledge_chunks AS profile_chunks
                        WHERE profile_chunks.organization_id = all_documents.organization_id
                          AND profile_chunks.document_id = all_documents.document_id
                          AND profile_chunks.embedding_profile_id = :embedding_profile_id
                          AND profile_chunks.embedding_provider_id = :embedding_provider_id
                          AND profile_chunks.embedding_model = :embedding_model
                          AND profile_chunks.embedding_dimensions = :embedding_dimensions
                          AND profile_chunks.embedding_index_version = :embedding_index_version
                    )
              )
        )
        SELECT authorized_chunks.chunk_id,
               authorized_chunks.document_id,
               authorized_chunks.content,
               authorized_chunks.source_reference,
               authorized_chunks.embedding <=> CAST(:query_embedding AS vector) AS distance
        FROM authorized_chunks
        ORDER BY distance ASC
        LIMIT :top_k
        """
    ).bindparams(
        organization_id=authorization_context.organization_id,
        allowed_scopes=list(authorization_context.scopes),
        allowed_classifications=list(authorization_context.classifications),
        knowledge_permissions=list(authorization_context.knowledge_permissions),
        embedding_profile_id=profile.profile_id,
        embedding_provider_id=profile.provider_id,
        embedding_model=profile.model,
        embedding_dimensions=profile.dimensions,
        embedding_index_version=profile.index_version,
        query_embedding=vector,
        top_k=limit,
    )


class KnowledgeRetriever:
    """PostgreSQL-backed retrieval. No local vector cache is consulted or maintained."""

    def __init__(self, engine: Engine, embedder: EmbeddingPort) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("Knowledge retrieval requires Cloud PostgreSQL")
        self._engine = engine
        profile = getattr(embedder, "profile", None)
        if not isinstance(profile, EmbeddingProfile):
            raise ValueError("knowledge embedding profile is required")
        self._embedder = embedder
        self._profile = profile

    def retrieve(
        self,
        query: str,
        authorization_context: KnowledgeAuthorizationContext,
        top_k: int,
    ) -> list[KnowledgeHit]:
        limit = _validated_top_k(top_k)
        if not isinstance(query, str) or not query.strip():
            raise ValueError("knowledge query is required")
        if not authorization_allows_retrieval(authorization_context):
            return []
        with self._engine.connect() as connection:
            total_documents = connection.execute(
                select(func.count())
                .select_from(knowledge_documents)
                .where(knowledge_documents.c.organization_id == authorization_context.organization_id)
            ).scalar_one()
            if total_documents == 0:
                return []
            profile_state = connection.execute(
                select(knowledge_embedding_profiles.c.state).where(
                    and_(
                        knowledge_embedding_profiles.c.organization_id
                        == authorization_context.organization_id,
                        knowledge_embedding_profiles.c.profile_id == self._profile.profile_id,
                        knowledge_embedding_profiles.c.index_version == self._profile.index_version,
                        knowledge_embedding_profiles.c.provider_id == self._profile.provider_id,
                        knowledge_embedding_profiles.c.model == self._profile.model,
                        knowledge_embedding_profiles.c.dimensions == self._profile.dimensions,
                    )
                )
            ).scalar_one_or_none()
            profiled_documents = connection.execute(
                select(func.count(func.distinct(knowledge_chunks.c.document_id)))
                .where(
                    and_(
                        knowledge_chunks.c.organization_id == authorization_context.organization_id,
                        knowledge_chunks.c.embedding_profile_id == self._profile.profile_id,
                        knowledge_chunks.c.embedding_provider_id == self._profile.provider_id,
                        knowledge_chunks.c.embedding_model == self._profile.model,
                        knowledge_chunks.c.embedding_dimensions == self._profile.dimensions,
                        knowledge_chunks.c.embedding_index_version == self._profile.index_version,
                    )
                )
            ).scalar_one()
        if profile_state != "READY" or profiled_documents != total_documents:
            raise EmbeddingIndexNotReadyError("knowledge embedding index is not ready")
        vectors = self._embedder.embed(
            [query],
            privacy=authorization_context.privacy,
            budget_ref=authorization_context.budget_ref,
            trace_id=authorization_context.trace_id,
            organization_id=authorization_context.organization_id,
            subject_id=authorization_context.subject_id,
            permissions=authorization_context.permissions,
        )
        if len(vectors) != 1:
            raise ValueError("embedding count does not match knowledge query")
        statement = build_retrieval_statement(authorization_context, self._profile, vectors[0], limit)
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [
            KnowledgeHit(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                content=row["content"],
                source_reference=dict(row["source_reference"]),
                distance=float(row["distance"]),
            )
            for row in rows
        ]


def _validated_top_k(top_k: int) -> int:
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 100:
        raise ValueError("knowledge top_k must be between 1 and 100")
    return top_k
