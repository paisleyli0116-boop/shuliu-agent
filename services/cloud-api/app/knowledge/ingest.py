from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Protocol

from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.engine import Engine

from app.persistence.postgres import (
    knowledge_chunks,
    knowledge_document_acl,
    knowledge_documents,
)
from .acl import KnowledgeAuthorizationContext


class ModelGatewayPort(Protocol):
    """Only the policy/budget/secret-aware ShuLiu Model Gateway is accepted here."""

    def complete(self, request: Mapping[str, object]) -> object: ...


class KnowledgeCacheInvalidator(Protocol):
    def invalidate_document(self, organization_id: str, document_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class KnowledgeChunkInput:
    content: str
    source_reference: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentInput:
    document_id: str
    organization_id: str
    scope: str
    classification: str
    metadata: Mapping[str, object]
    chunks: tuple[KnowledgeChunkInput, ...]
    permission_codes: tuple[str, ...]
    subject_id: str
    permissions: tuple[str, ...]
    privacy: str
    budget_ref: str
    trace_id: str


def ingest_document(
    engine: Engine,
    document: KnowledgeDocumentInput,
    model_gateway: ModelGatewayPort,
    cache_invalidator: KnowledgeCacheInvalidator,
) -> tuple[str, ...]:
    _require_cloud_postgres(engine)
    _validate_document(document)
    embeddings = tuple(
        _embed_text(model_gateway, document, chunk.content) for chunk in document.chunks
    )
    now = datetime.now(timezone.utc)

    with engine.begin() as connection:
        exists = connection.execute(
            select(knowledge_documents.c.document_id).where(
                and_(
                    knowledge_documents.c.organization_id == document.organization_id,
                    knowledge_documents.c.document_id == document.document_id,
                )
            )
        ).first()
        if exists:
            connection.execute(
                delete(knowledge_chunks).where(
                    and_(
                        knowledge_chunks.c.organization_id == document.organization_id,
                        knowledge_chunks.c.document_id == document.document_id,
                    )
                )
            )
            connection.execute(
                delete(knowledge_document_acl).where(
                    and_(
                        knowledge_document_acl.c.organization_id == document.organization_id,
                        knowledge_document_acl.c.document_id == document.document_id,
                    )
                )
            )
            connection.execute(
                update(knowledge_documents)
                .where(
                    and_(
                        knowledge_documents.c.organization_id == document.organization_id,
                        knowledge_documents.c.document_id == document.document_id,
                    )
                )
                .values(
                    scope=document.scope,
                    classification=document.classification,
                    metadata=dict(document.metadata),
                    created_at=now,
                )
            )
        else:
            connection.execute(
                insert(knowledge_documents).values(
                    document_id=document.document_id,
                    organization_id=document.organization_id,
                    scope=document.scope,
                    classification=document.classification,
                    metadata=dict(document.metadata),
                    created_at=now,
                )
            )

        connection.execute(
            insert(knowledge_chunks),
            [
                {
                    "chunk_id": f"{document.document_id}:{index}",
                    "organization_id": document.organization_id,
                    "document_id": document.document_id,
                    "chunk_index": index,
                    "content": chunk.content,
                    "source_reference": dict(chunk.source_reference),
                    "embedding": embedding,
                }
                for index, (chunk, embedding) in enumerate(zip(document.chunks, embeddings))
            ],
        )
        if document.permission_codes:
            connection.execute(
                insert(knowledge_document_acl),
                [
                    {
                        "organization_id": document.organization_id,
                        "document_id": document.document_id,
                        "permission_code": code,
                    }
                    for code in sorted(set(document.permission_codes))
                ],
            )

    cache_invalidator.invalidate_document(document.organization_id, document.document_id)
    return tuple(f"{document.document_id}:{index}" for index in range(len(document.chunks)))


def replace_document_permissions(
    engine: Engine,
    organization_id: str,
    document_id: str,
    permission_codes: Sequence[str],
    cache_invalidator: KnowledgeCacheInvalidator,
) -> None:
    _require_cloud_postgres(engine)
    if not _non_empty(organization_id) or not _non_empty(document_id):
        raise ValueError("knowledge document identity is invalid")
    if not all(_non_empty(code) for code in permission_codes):
        raise ValueError("knowledge permission code is invalid")
    with engine.begin() as connection:
        exists = connection.execute(
            select(knowledge_documents.c.document_id).where(
                and_(
                    knowledge_documents.c.organization_id == organization_id,
                    knowledge_documents.c.document_id == document_id,
                )
            )
        ).first()
        if not exists:
            raise KeyError("knowledge document not found")
        connection.execute(
            delete(knowledge_document_acl).where(
                and_(
                    knowledge_document_acl.c.organization_id == organization_id,
                    knowledge_document_acl.c.document_id == document_id,
                )
            )
        )
        if permission_codes:
            connection.execute(
                insert(knowledge_document_acl),
                [
                    {
                        "organization_id": organization_id,
                        "document_id": document_id,
                        "permission_code": code,
                    }
                    for code in sorted(set(permission_codes))
                ],
            )
    cache_invalidator.invalidate_document(organization_id, document_id)


def delete_document(
    engine: Engine,
    organization_id: str,
    document_id: str,
    cache_invalidator: KnowledgeCacheInvalidator,
) -> bool:
    _require_cloud_postgres(engine)
    with engine.begin() as connection:
        connection.execute(
            delete(knowledge_chunks).where(
                and_(
                    knowledge_chunks.c.organization_id == organization_id,
                    knowledge_chunks.c.document_id == document_id,
                )
            )
        )
        connection.execute(
            delete(knowledge_document_acl).where(
                and_(
                    knowledge_document_acl.c.organization_id == organization_id,
                    knowledge_document_acl.c.document_id == document_id,
                )
            )
        )
        result = connection.execute(
            delete(knowledge_documents).where(
                and_(
                    knowledge_documents.c.organization_id == organization_id,
                    knowledge_documents.c.document_id == document_id,
                )
            )
        )
    if result.rowcount:
        cache_invalidator.invalidate_document(organization_id, document_id)
    return bool(result.rowcount)


def _embed_text(
    model_gateway: ModelGatewayPort,
    document: KnowledgeDocumentInput,
    content: str,
) -> tuple[float, ...]:
    result = model_gateway.complete(
        {
            "capability": "embedding",
            "privacy": document.privacy,
            "budget_ref": document.budget_ref,
            "input": content,
            "trace_id": document.trace_id,
            "organization_id": document.organization_id,
            "subject_id": document.subject_id,
            "permissions": document.permissions,
        }
    )
    output = getattr(result, "output", None)
    if not isinstance(output, Sequence) or isinstance(output, (str, bytes)) or len(output) != 1:
        raise ValueError("embedding gateway response is invalid")
    return _validated_vector(output[0])


def _validate_document(document: KnowledgeDocumentInput) -> None:
    for value in (
        document.document_id,
        document.organization_id,
        document.scope,
        document.classification,
        document.subject_id,
        document.privacy,
        document.budget_ref,
        document.trace_id,
    ):
        if not _non_empty(value):
            raise ValueError("knowledge document input is invalid")
    if not isinstance(document.chunks, tuple) or not document.chunks:
        raise ValueError("knowledge document must contain chunks")
    if not all(
        isinstance(chunk, KnowledgeChunkInput)
        and _non_empty(chunk.content)
        and isinstance(chunk.source_reference, Mapping)
        for chunk in document.chunks
    ):
        raise ValueError("knowledge chunk input is invalid")
    if not all(
        _non_empty(value) and value.startswith("knowledge.")
        for value in document.permission_codes
    ):
        raise ValueError("knowledge permission code is invalid")
    if not document.permissions or not all(_non_empty(value) for value in document.permissions):
        raise ValueError("knowledge model authorization context is invalid")
    if not isinstance(document.metadata, Mapping):
        raise ValueError("knowledge document metadata is invalid")


def _validated_vector(value: object) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ValueError("embedding vector is invalid")
    result: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not isfinite(float(item)):
            raise ValueError("embedding vector is invalid")
        result.append(float(item))
    return tuple(result)


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_cloud_postgres(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        raise ValueError("Knowledge persistence requires Cloud PostgreSQL")
