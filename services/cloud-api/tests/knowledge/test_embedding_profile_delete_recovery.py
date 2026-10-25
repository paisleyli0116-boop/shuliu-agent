from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, insert, select

import app.knowledge.ingest as ingest
from app.persistence.postgres import (
    knowledge_chunks,
    knowledge_documents,
    knowledge_embedding_profiles,
)


SERVICE_ROOT = Path(__file__).resolve().parents[2]


class NoopInvalidator:
    def invalidate_document(self, _organization_id: str, _document_id: str) -> None:
        return None


def test_deleting_last_unprofiled_document_marks_complete_embedding_profile_ready(monkeypatch):
    # Exercise the real delete function and migrated relational constraints while
    # replacing only the PostgreSQL-specific engine guard/advisory lock.
    monkeypatch.setattr(ingest, "_require_cloud_postgres", lambda _engine: None)
    monkeypatch.setattr(ingest, "_lock_organization_index", lambda _connection, _org: None)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    organization_id = "org-delete-recovery"
    now = datetime.now(timezone.utc)
    try:
        with engine.begin() as connection:
            config = Config(str(SERVICE_ROOT / "alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.execute(
                insert(knowledge_documents),
                [
                    {
                        "document_id": "doc-profiled",
                        "organization_id": organization_id,
                        "scope": "team:test",
                        "classification": "PUBLIC",
                        "metadata": {"test": True},
                        "created_at": now,
                    },
                    {
                        "document_id": "doc-unprofiled",
                        "organization_id": organization_id,
                        "scope": "team:test",
                        "classification": "PUBLIC",
                        "metadata": {"test": True},
                        "created_at": now,
                    },
                ],
            )
            connection.execute(
                insert(knowledge_chunks),
                [
                    {
                        "chunk_id": "chunk-profiled",
                        "organization_id": organization_id,
                        "document_id": "doc-profiled",
                        "chunk_index": 0,
                        "content": "public fixture",
                        "source_reference": {"source": "test"},
                        "embedding": (0.1, 0.2, 0.3),
                        "embedding_profile_id": "profile-v1",
                        "embedding_provider_id": "provider-a",
                        "embedding_model": "embed-v1",
                        "embedding_dimensions": 3,
                        "embedding_index_version": 1,
                    },
                    {
                        "chunk_id": "chunk-unprofiled",
                        "organization_id": organization_id,
                        "document_id": "doc-unprofiled",
                        "chunk_index": 0,
                        "content": "legacy public fixture",
                        "source_reference": {"source": "test"},
                        "embedding": (0.3, 0.2, 0.1),
                        "embedding_profile_id": None,
                        "embedding_provider_id": None,
                        "embedding_model": None,
                        "embedding_dimensions": None,
                        "embedding_index_version": None,
                    },
                ],
            )
            connection.execute(
                insert(knowledge_embedding_profiles).values(
                    organization_id=organization_id,
                    profile_id="profile-v1",
                    index_version=1,
                    provider_id="provider-a",
                    model="embed-v1",
                    dimensions=3,
                    state="BUILDING",
                    updated_at=now,
                )
            )

        assert ingest.delete_document(
            engine, organization_id, "doc-unprofiled", NoopInvalidator()
        )

        with engine.connect() as connection:
            profile_state = connection.execute(
                select(knowledge_embedding_profiles.c.state).where(
                    knowledge_embedding_profiles.c.organization_id == organization_id
                )
            ).scalar_one()
        assert profile_state == "READY"
    finally:
        engine.dispose()


def test_mixed_profile_chunks_keep_profile_building_after_last_unprofiled_document_is_deleted(monkeypatch):
    monkeypatch.setattr(ingest, "_require_cloud_postgres", lambda _engine: None)
    monkeypatch.setattr(ingest, "_lock_organization_index", lambda _connection, _org: None)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    organization_id = "org-mixed-profile-delete-recovery"
    now = datetime.now(timezone.utc)
    try:
        with engine.begin() as connection:
            config = Config(str(SERVICE_ROOT / "alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.execute(
                insert(knowledge_documents),
                [
                    {
                        "document_id": "doc-mixed",
                        "organization_id": organization_id,
                        "scope": "team:test",
                        "classification": "PUBLIC",
                        "metadata": {"test": True},
                        "created_at": now,
                    },
                    {
                        "document_id": "doc-unprofiled",
                        "organization_id": organization_id,
                        "scope": "team:test",
                        "classification": "PUBLIC",
                        "metadata": {"test": True},
                        "created_at": now,
                    },
                ],
            )
            connection.execute(
                insert(knowledge_chunks),
                [
                    {
                        "chunk_id": "chunk-current",
                        "organization_id": organization_id,
                        "document_id": "doc-mixed",
                        "chunk_index": 0,
                        "content": "profile current chunk",
                        "source_reference": {"source": "test"},
                        "embedding": (0.1, 0.2, 0.3),
                        "embedding_profile_id": "profile-v1",
                        "embedding_provider_id": "provider-a",
                        "embedding_model": "embed-v1",
                        "embedding_dimensions": 3,
                        "embedding_index_version": 1,
                    },
                    {
                        "chunk_id": "chunk-old",
                        "organization_id": organization_id,
                        "document_id": "doc-mixed",
                        "chunk_index": 1,
                        "content": "profile old chunk",
                        "source_reference": {"source": "test"},
                        "embedding": (0.3, 0.2, 0.1),
                        "embedding_profile_id": "profile-old",
                        "embedding_provider_id": "provider-old",
                        "embedding_model": "embed-old",
                        "embedding_dimensions": 3,
                        "embedding_index_version": 1,
                    },
                    {
                        "chunk_id": "chunk-unprofiled",
                        "organization_id": organization_id,
                        "document_id": "doc-unprofiled",
                        "chunk_index": 0,
                        "content": "legacy public fixture",
                        "source_reference": {"source": "test"},
                        "embedding": (0.3, 0.2, 0.1),
                        "embedding_profile_id": None,
                        "embedding_provider_id": None,
                        "embedding_model": None,
                        "embedding_dimensions": None,
                        "embedding_index_version": None,
                    },
                ],
            )
            connection.execute(
                insert(knowledge_embedding_profiles).values(
                    organization_id=organization_id,
                    profile_id="profile-v1",
                    index_version=1,
                    provider_id="provider-a",
                    model="embed-v1",
                    dimensions=3,
                    state="BUILDING",
                    updated_at=now,
                )
            )

        assert ingest.delete_document(
            engine, organization_id, "doc-unprofiled", NoopInvalidator()
        )

        with engine.connect() as connection:
            profile_state = connection.execute(
                select(knowledge_embedding_profiles.c.state).where(
                    knowledge_embedding_profiles.c.organization_id == organization_id
                )
            ).scalar_one()
        assert profile_state == "BUILDING"
    finally:
        engine.dispose()
