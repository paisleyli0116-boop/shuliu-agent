from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .acl import (
    KnowledgeAuthorizationContext,
    authorization_allows_retrieval,
    authorized_document_predicate,
)
from .ingest import ModelGatewayPort


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    chunk_id: str
    document_id: str
    content: str
    source_reference: Mapping[str, object]
    distance: float


def build_retrieval_statement(
    authorization_context: KnowledgeAuthorizationContext,
    query_embedding: Sequence[float],
    top_k: int,
):
    if not authorization_allows_retrieval(authorization_context):
        raise ValueError("knowledge authorization context is incomplete")
    vector = _validated_vector(query_embedding)
    limit = _validated_top_k(top_k)
    return text(
        f"""
        WITH authorized_chunks AS MATERIALIZED (
            SELECT knowledge_chunks.chunk_id,
                   knowledge_chunks.document_id,
                   knowledge_chunks.content,
                   knowledge_chunks.source_reference,
                   knowledge_chunks.embedding
            FROM knowledge_chunks
            JOIN knowledge_documents
              ON knowledge_documents.organization_id = knowledge_chunks.organization_id
             AND knowledge_documents.document_id = knowledge_chunks.document_id
            WHERE {authorized_document_predicate()}
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
        query_embedding=vector,
        top_k=limit,
    )


class KnowledgeRetriever:
    """PostgreSQL-backed retrieval. No local vector cache is consulted or maintained."""

    def __init__(self, engine: Engine, model_gateway: ModelGatewayPort) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("Knowledge retrieval requires Cloud PostgreSQL")
        self._engine = engine
        self._model_gateway = model_gateway

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
        result = self._model_gateway.complete(
            {
                "capability": "embedding",
                "privacy": authorization_context.privacy,
                "budget_ref": authorization_context.budget_ref,
                "input": query,
                "trace_id": authorization_context.trace_id,
                "organization_id": authorization_context.organization_id,
                "subject_id": authorization_context.subject_id,
                "permissions": authorization_context.permissions,
            }
        )
        output = getattr(result, "output", None)
        if not isinstance(output, Sequence) or isinstance(output, (str, bytes)) or len(output) != 1:
            raise ValueError("embedding gateway response is invalid")
        statement = build_retrieval_statement(authorization_context, output[0], limit)
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


def _validated_vector(value: object) -> str:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ValueError("embedding vector is invalid")
    values: list[str] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not isfinite(float(item)):
            raise ValueError("embedding vector is invalid")
        values.append(format(float(item), ".17g"))
    return "[" + ",".join(values) + "]"


def _validated_top_k(top_k: int) -> int:
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 100:
        raise ValueError("knowledge top_k must be between 1 and 100")
    return top_k
