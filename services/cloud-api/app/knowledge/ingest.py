from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Protocol

from sqlalchemy import and_, delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Engine

from app.persistence.postgres import (
    knowledge_chunks,
    knowledge_document_acl,
    knowledge_documents,
    knowledge_embedding_profiles,
)
from .embedding import (
    EmbeddingConfigurationError,
    EmbeddingPort,
    EmbeddingProfile,
    _validated_vector,
)


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
    embedder: EmbeddingPort,
    cache_invalidator: KnowledgeCacheInvalidator,
) -> tuple[str, ...]:
    _require_cloud_postgres(engine)
    _validate_document(document)
    profile = getattr(embedder, "profile", None)
    if not isinstance(profile, EmbeddingProfile):
        raise ValueError("knowledge embedding profile is required")
    if document.privacy not in profile.privacy_levels:
        raise EmbeddingConfigurationError("embedding profile does not allow document privacy")
    _register_embedding_profile(engine, document.organization_id, profile)
    embeddings = embedder.embed(
        [chunk.content for chunk in document.chunks],
        privacy=document.privacy,
        budget_ref=document.budget_ref,
        trace_id=document.trace_id,
        organization_id=document.organization_id,
        subject_id=document.subject_id,
        permissions=document.permissions,
    )
    if len(embeddings) != len(document.chunks):
        raise ValueError("embedding count does not match knowledge chunks")
    embeddings = tuple(_validated_vector(vector, profile.dimensions) for vector in embeddings)
    now = datetime.now(timezone.utc)

    with engine.begin() as connection:
        _lock_organization_index(connection, document.organization_id)
        _assert_registered_profile(connection, document.organization_id, profile)
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
                    "embedding_profile_id": profile.profile_id,
                    "embedding_provider_id": profile.provider_id,
                    "embedding_model": profile.model,
                    "embedding_dimensions": profile.dimensions,
                    "embedding_index_version": profile.index_version,
                }
                for index, (chunk, embedding) in enumerate(zip(document.chunks, embeddings, strict=True))
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

        _refresh_embedding_profile_states(connection, document.organization_id)

    cache_invalidator.invalidate_document(document.organization_id, document.document_id)
    return tuple(f"{document.document_id}:{index}" for index in range(len(document.chunks)))


def _register_embedding_profile(
    engine: Engine, organization_id: str, profile: EmbeddingProfile
) -> None:
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        _lock_organization_index(connection, organization_id)
        connection.execute(
            postgresql_insert(knowledge_embedding_profiles)
            .values(
                organization_id=organization_id,
                profile_id=profile.profile_id,
                index_version=profile.index_version,
                provider_id=profile.provider_id,
                model=profile.model,
                dimensions=profile.dimensions,
                state="BUILDING",
                updated_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    knowledge_embedding_profiles.c.organization_id,
                    knowledge_embedding_profiles.c.profile_id,
                    knowledge_embedding_profiles.c.index_version,
                ]
            )
        )
        _assert_registered_profile(connection, organization_id, profile)


def _assert_registered_profile(connection, organization_id: str, profile: EmbeddingProfile) -> None:
    row = connection.execute(
        select(knowledge_embedding_profiles)
        .where(
            and_(
                knowledge_embedding_profiles.c.organization_id == organization_id,
                knowledge_embedding_profiles.c.profile_id == profile.profile_id,
                knowledge_embedding_profiles.c.index_version == profile.index_version,
            )
        )
        .with_for_update()
    ).mappings().first()
    if row is None or (
        row["provider_id"], row["model"], row["dimensions"]
    ) != (profile.provider_id, profile.model, profile.dimensions):
        raise ValueError("embedding profile version conflicts with registered index")


def _refresh_embedding_profile_states(connection, organization_id: str) -> None:
    total_documents = connection.execute(
        select(func.count()).select_from(knowledge_documents).where(
            knowledge_documents.c.organization_id == organization_id
        )
    ).scalar_one()
    registered_profiles = connection.execute(
        select(knowledge_embedding_profiles).where(
            knowledge_embedding_profiles.c.organization_id == organization_id
        )
    ).mappings().all()
    total_chunks = connection.execute(
        select(func.count()).select_from(knowledge_chunks).where(
            knowledge_chunks.c.organization_id == organization_id
        )
    ).scalar_one()
    for registered in registered_profiles:
        profiled_documents = connection.execute(
            select(func.count(func.distinct(knowledge_chunks.c.document_id))).where(
                and_(
                    knowledge_chunks.c.organization_id == organization_id,
                    knowledge_chunks.c.embedding_profile_id == registered["profile_id"],
                    knowledge_chunks.c.embedding_provider_id == registered["provider_id"],
                    knowledge_chunks.c.embedding_model == registered["model"],
                    knowledge_chunks.c.embedding_dimensions == registered["dimensions"],
                    knowledge_chunks.c.embedding_index_version == registered["index_version"],
                )
            )
        ).scalar_one()
        matching_chunks = connection.execute(
            select(func.count()).select_from(knowledge_chunks).where(
                and_(
                    knowledge_chunks.c.organization_id == organization_id,
                    knowledge_chunks.c.embedding_profile_id == registered["profile_id"],
                    knowledge_chunks.c.embedding_provider_id == registered["provider_id"],
                    knowledge_chunks.c.embedding_model == registered["model"],
                    knowledge_chunks.c.embedding_dimensions == registered["dimensions"],
                    knowledge_chunks.c.embedding_index_version == registered["index_version"],
                )
            )
        ).scalar_one()
        connection.execute(
            update(knowledge_embedding_profiles)
            .where(
                and_(
                    knowledge_embedding_profiles.c.organization_id == organization_id,
                    knowledge_embedding_profiles.c.profile_id == registered["profile_id"],
                    knowledge_embedding_profiles.c.index_version == registered["index_version"],
                )
            )
            .values(
                state="READY"
                if (
                    total_documents > 0
                    and profiled_documents == total_documents
                    and matching_chunks == total_chunks
                )
                else "BUILDING",
                updated_at=datetime.now(timezone.utc),
            )
        )


def _lock_organization_index(connection, organization_id: str) -> None:
    lock_id = int.from_bytes(sha256(organization_id.encode("utf-8")).digest()[:8], "big", signed=True)
    connection.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id}
    )


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
        _lock_organization_index(connection, organization_id)
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
            _refresh_embedding_profile_states(connection, organization_id)
    if result.rowcount:
        cache_invalidator.invalidate_document(organization_id, document_id)
    return bool(result.rowcount)


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
    if (
        not isinstance(document.permissions, tuple)
        or not document.permissions
        or not all(_non_empty(value) for value in document.permissions)
    ):
        raise ValueError("knowledge model authorization context is invalid")
    if not isinstance(document.metadata, Mapping):
        raise ValueError("knowledge document metadata is invalid")


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_cloud_postgres(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        raise ValueError("Knowledge persistence requires Cloud PostgreSQL")
