from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tomllib
from typing import Any
from uuid import uuid4
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select

from app.knowledge.acl import KnowledgeAuthorizationContext
from app.knowledge.embedding import EmbeddingProfile
from app.knowledge.retrieval import KnowledgeRetriever
from app.persistence.postgres import (
    create_postgres_engine,
    knowledge_chunks,
    knowledge_documents,
    knowledge_embedding_profiles,
)
from app.knowledge.parsers.base import ParsedDocument, ParsedTextSection, read_bounded_stream
from app.knowledge.document_ingestion import DocumentIngestionInput, ingest_file
from app.knowledge.parsers.base import ParserLimits


@dataclass(frozen=True)
class FakeEmbedder:
    profile: object = object()


class FakeInvalidator:
    def invalidate_document(self, organization_id: str, document_id: str) -> None:
        raise AssertionError("the unit test must not touch persistence cache state")


def _context(**overrides: Any) -> KnowledgeAuthorizationContext:
    values: dict[str, Any] = {
        "organization_id": "org-a",
        "subject_id": "user-a",
        "scopes": ("team:finance",),
        "classifications": ("INTERNAL",),
        "permissions": ("knowledge.write", "foundation.model.invoke"),
        "knowledge_permissions": ("knowledge.read",),
        "privacy": "INTERNAL",
        "budget_ref": "budget://org-a/knowledge",
        "trace_id": "trace-ingest-file-1",
    }
    values.update(overrides)
    return KnowledgeAuthorizationContext(**values)


def _input(**overrides: Any) -> DocumentIngestionInput:
    values: dict[str, Any] = {
        "file_stream": b"untrusted document bytes",
        "filename": "policy.docx",
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "document_id": "doc-1",
        "scope": "team:finance",
        "classification": "INTERNAL",
        "metadata": {"title": "Finance policy"},
        "permission_codes": ("knowledge.read",),
    }
    values.update(overrides)
    return DocumentIngestionInput(**values)


class _ShortReadStream:
    def __init__(self, payload: bytes, chunk_size: int = 3) -> None:
        self._payload = payload
        self._position = 0
        self._chunk_size = chunk_size

    def read(self, size: int = -1) -> bytes:
        if self._position >= len(self._payload):
            return b""
        count = min(size, self._chunk_size, len(self._payload) - self._position)
        chunk = self._payload[self._position : self._position + count]
        self._position += count
        return chunk


def test_bounded_stream_collects_legal_short_reads_until_eof() -> None:
    payload = b"a complete document stream"

    assert read_bounded_stream(_ShortReadStream(payload), len(payload)) == payload


def test_ingest_file_passes_normalized_cited_chunks_and_trusted_context_to_task30_ingest(monkeypatch) -> None:
    import app.knowledge.document_ingestion as module

    calls: list[tuple[object, object, object, object, object]] = []
    monkeypatch.setattr(
        module,
        "parse_document",
        lambda *_args, **_kwargs: ParsedDocument(
            text_sections=(
                ParsedTextSection(
                    text="  \uff26\uff49\uff4e\uff41\uff4e\uff43\uff45\r\npolicy  ",
                    source_reference={"part": "word/document.xml", "paragraph": 1},
                ),
            )
        ),
    )
    monkeypatch.setattr(
        module,
        "ingest_document",
        lambda engine, document, embedder, invalidator: calls.append(
            (engine, document, embedder, invalidator, document.trace_id)
        ),
    )
    engine = object()
    embedder = FakeEmbedder()
    invalidator = FakeInvalidator()

    result = ingest_file(engine, _input(), _context(), embedder, invalidator)

    assert result == "doc-1"
    assert len(calls) == 1
    passed_engine, document, passed_embedder, passed_invalidator, trace_id = calls[0]
    assert passed_engine is engine
    assert passed_embedder is embedder
    assert passed_invalidator is invalidator
    assert trace_id == "trace-ingest-file-1"
    assert document.organization_id == "org-a"
    assert document.subject_id == "user-a"
    assert document.scope == "team:finance"
    assert document.classification == "INTERNAL"
    assert document.permission_codes == ("knowledge.read",)
    assert document.permissions == ("knowledge.write", "foundation.model.invoke")
    assert document.chunks[0].content == "Finance\npolicy"
    assert document.chunks[0].source_reference == {
        "part": "word/document.xml", "paragraph": 1, "start_char": 0, "end_char": 14
    }


def test_ingest_file_rejects_scope_and_permissions_outside_trusted_context_before_parsing(monkeypatch) -> None:
    import app.knowledge.document_ingestion as module

    called = False

    def parser_must_not_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("unauthorized upload was parsed")

    monkeypatch.setattr(module, "parse_document", parser_must_not_run)
    with pytest.raises(ValueError, match="authorization"):
        ingest_file(
            object(),
            _input(scope="team:legal"),
            _context(),
            FakeEmbedder(),
            FakeInvalidator(),
        )
    assert called is False

    with pytest.raises(ValueError, match="authorization"):
        ingest_file(
            object(),
            _input(permission_codes=("knowledge.admin",)),
            _context(),
            FakeEmbedder(),
            FakeInvalidator(),
        )
    assert called is False

    with pytest.raises(ValueError, match="authorization"):
        ingest_file(
            object(),
            _input(),
            _context(organization_id=""),
            FakeEmbedder(),
            FakeInvalidator(),
        )
    assert called is False


def test_ingest_file_does_not_create_document_when_parsing_fails(monkeypatch) -> None:
    import app.knowledge.document_ingestion as module

    ingestion_calls = 0

    def parse_failure(*_args, **_kwargs):
        from app.knowledge.parsers.base import DocumentParseError

        raise DocumentParseError("DOCUMENT_MALFORMED", "document package is malformed")

    def forbidden_ingest(*_args, **_kwargs):
        nonlocal ingestion_calls
        ingestion_calls += 1

    monkeypatch.setattr(module, "parse_document", parse_failure)
    monkeypatch.setattr(module, "ingest_document", forbidden_ingest)

    with pytest.raises(module.DocumentParseError) as error:
        ingest_file(object(), _input(), _context(), FakeEmbedder(), FakeInvalidator())

    assert error.value.code == "DOCUMENT_MALFORMED"
    assert ingestion_calls == 0


def test_ingest_file_enforces_chunk_limit_before_task30_ingest(monkeypatch) -> None:
    import app.knowledge.document_ingestion as module

    ingestion_calls = 0

    monkeypatch.setattr(
        module,
        "parse_document",
        lambda *_args, **_kwargs: ParsedDocument(
            text_sections=(ParsedTextSection("one two three four", {"page": 1}),)
        ),
    )

    def forbidden_ingest(*_args, **_kwargs):
        nonlocal ingestion_calls
        ingestion_calls += 1

    monkeypatch.setattr(module, "ingest_document", forbidden_ingest)
    with pytest.raises(module.DocumentParseError) as too_many:
        ingest_file(
            object(), _input(), _context(), FakeEmbedder(), FakeInvalidator(),
            limits=ParserLimits(max_chunk_characters=5, max_chunks=1),
        )

    assert too_many.value.code == "DOCUMENT_CHUNK_LIMIT_EXCEEDED"
    assert ingestion_calls == 0


def test_parser_dependencies_are_locked_and_have_approved_license_evidence() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    package_root = repository_root / "services" / "cloud-api"
    package = tomllib.loads((package_root / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((repository_root / "uv.lock").read_text(encoding="utf-8"))
    policy = json.loads((repository_root / "tests" / "release" / "dependency-license-policy.yaml").read_text(encoding="utf-8"))

    expected = {"pypdf": "6.21.2", "defusedxml": "0.7.1"}
    direct_dependencies = set(package["project"]["dependencies"])
    lock_versions = {entry["name"]: entry["version"] for entry in lock["package"]}
    reviewed = {entry["name"]: entry for entry in policy["dependencies"]}
    assert all(any(dependency.startswith(f"{name}=={version}") for dependency in direct_dependencies)
               for name, version in expected.items())
    assert all(lock_versions.get(name) == version for name, version in expected.items())
    assert set(reviewed) == set(expected)
    assert all(entry["license"] in policy["allowed_spdx_licenses"] for entry in reviewed.values())
    assert policy["approval_status"] == "APPROVED"
    approval_record = repository_root / policy["approval_record"]
    assert approval_record.is_file()
    assert "APPROVED" in approval_record.read_text(encoding="utf-8")


def test_docx_ingestion_persists_citations_and_is_retrievable_in_postgres() -> None:
    database_url = os.environ.get("SHULIU_DATABASE_URL")
    if not database_url:
        pytest.skip("SHULIU_DATABASE_URL is not configured")

    service_root = Path(__file__).resolve().parents[2]
    engine = create_postgres_engine(database_url)
    organization_id = f"task31-{uuid4().hex}"
    document_id = f"doc-{uuid4().hex}"
    profile = EmbeddingProfile(
        profile_id="task31-parser-integration",
        provider_id="fake-provider",
        model="fake/embedding-v1",
        dimensions=3,
        privacy_levels=frozenset({"PUBLIC"}),
        index_version=1,
    )

    class IntegrationEmbedder:
        def __init__(self) -> None:
            self.profile = profile

        def embed(self, texts, **_context):
            return tuple((1.0, 0.0, 0.0) for _ in texts)

    class NoopInvalidator:
        def invalidate_document(self, _organization_id: str, _document_id: str) -> None:
            return None

    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" ContentType="'
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Public integration evidence</w:t></w:r></w:p></w:body>"
            "</w:document>",
        )

    context = _context(
        organization_id=organization_id,
        scopes=("team:test",),
        classifications=("PUBLIC",),
        privacy="PUBLIC",
        budget_ref=f"budget://{organization_id}/knowledge",
        trace_id=f"trace-{uuid4().hex}",
    )
    ingestion_input = _input(
        file_stream=buffer.getvalue(),
        filename="integration.docx",
        document_id=document_id,
        scope="team:test",
        classification="PUBLIC",
        metadata={"purpose": "task31-integration"},
    )
    embedder = IntegrationEmbedder()
    schema_ready = False

    try:
        with engine.begin() as connection:
            config = Config(str(service_root / "alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        schema_ready = True

        ingest_file(engine, ingestion_input, context, embedder, NoopInvalidator())
        with engine.connect() as connection:
            stored_document = connection.execute(
                select(knowledge_documents.c.document_id).where(
                    knowledge_documents.c.organization_id == organization_id,
                    knowledge_documents.c.document_id == document_id,
                )
            ).scalar_one()
            stored_chunk = connection.execute(
                select(knowledge_chunks.c.content, knowledge_chunks.c.source_reference).where(
                    knowledge_chunks.c.organization_id == organization_id,
                    knowledge_chunks.c.document_id == document_id,
                )
            ).one()

        assert stored_document == document_id
        assert stored_chunk.content == "Public integration evidence"
        assert stored_chunk.source_reference == {
            "part": "word/document.xml", "paragraph": 1, "start_char": 0, "end_char": 27
        }
        hits = KnowledgeRetriever(engine, embedder).retrieve("public query", context, 5)
        assert [hit.document_id for hit in hits] == [document_id]
        assert hits[0].source_reference == stored_chunk.source_reference
    finally:
        try:
            if schema_ready:
                with engine.begin() as connection:
                    connection.execute(
                        delete(knowledge_documents).where(knowledge_documents.c.organization_id == organization_id)
                    )
                    connection.execute(
                        delete(knowledge_embedding_profiles).where(
                            knowledge_embedding_profiles.c.organization_id == organization_id
                        )
                    )
        finally:
            engine.dispose()
