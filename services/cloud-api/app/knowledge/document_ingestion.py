from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from typing import BinaryIO
from sqlalchemy.engine import Engine

from .acl import KnowledgeAuthorizationContext, authorization_allows_retrieval
from .embedding import EmbeddingPort
from .ingest import KnowledgeCacheInvalidator, KnowledgeChunkInput, KnowledgeDocumentInput, ingest_document
from .parsers import DocumentParseError, ParsedDocument, ParsedTextSection, ParserLimits, parse_document
from .parsers.base import normalize_text


_WRITE_PERMISSION = "knowledge.write"
_MAX_METADATA_BYTES = 16 * 1024


@dataclass(frozen=True, slots=True)
class DocumentIngestionInput:
    file_stream: BinaryIO | bytes | bytearray | memoryview
    filename: str
    mime_type: str
    document_id: str
    scope: str
    classification: str
    metadata: Mapping[str, object]
    permission_codes: tuple[str, ...]


def ingest_file(
    engine: Engine,
    ingestion_input: DocumentIngestionInput,
    trusted_authorization_context: KnowledgeAuthorizationContext,
    embedder: EmbeddingPort,
    cache_invalidator: KnowledgeCacheInvalidator,
    *,
    limits: ParserLimits | None = None,
) -> str:
    """Parse a bounded file, then pass only normalized cited chunks through Task 30 ingest."""
    _validate_ingestion_input(ingestion_input, trusted_authorization_context)
    parsed = parse_document(
        ingestion_input.file_stream,
        filename=ingestion_input.filename,
        mime_type=ingestion_input.mime_type,
        limits=limits,
    )
    chunks = _chunk_sections(parsed, limits or ParserLimits())
    document = KnowledgeDocumentInput(
        document_id=ingestion_input.document_id,
        organization_id=trusted_authorization_context.organization_id,
        scope=ingestion_input.scope,
        classification=ingestion_input.classification,
        metadata=dict(ingestion_input.metadata),
        chunks=chunks,
        permission_codes=ingestion_input.permission_codes,
        subject_id=trusted_authorization_context.subject_id,
        permissions=trusted_authorization_context.permissions,
        privacy=trusted_authorization_context.privacy,
        budget_ref=trusted_authorization_context.budget_ref,
        trace_id=trusted_authorization_context.trace_id,
    )
    ingest_document(engine, document, embedder, cache_invalidator)
    return ingestion_input.document_id


def _validate_ingestion_input(
    ingestion_input: DocumentIngestionInput,
    context: KnowledgeAuthorizationContext,
) -> None:
    if type(ingestion_input) is not DocumentIngestionInput or type(context) is not KnowledgeAuthorizationContext:
        raise ValueError("knowledge ingestion authorization is invalid")
    if (
        not _non_empty(ingestion_input.document_id)
        or len(ingestion_input.document_id) > 128
        or not _non_empty(ingestion_input.filename)
        or not _non_empty(ingestion_input.scope)
        or not _non_empty(ingestion_input.classification)
        or not isinstance(ingestion_input.metadata, Mapping)
        or not isinstance(ingestion_input.permission_codes, tuple)
        or not ingestion_input.permission_codes
    ):
        raise ValueError("knowledge ingestion input is invalid")
    if (
        not authorization_allows_retrieval(context)
        or _WRITE_PERMISSION not in context.permissions
        or ingestion_input.scope not in context.scopes
        or ingestion_input.classification not in context.classifications
        or not all(
            isinstance(code, str)
            and code.startswith("knowledge.")
            and code in context.knowledge_permissions
            for code in ingestion_input.permission_codes
        )
    ):
        raise ValueError("knowledge ingestion authorization is invalid")
    try:
        encoded_metadata = json.dumps(dict(ingestion_input.metadata), allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError):
        raise ValueError("knowledge ingestion metadata is invalid") from None
    if len(encoded_metadata.encode("utf-8")) > _MAX_METADATA_BYTES:
        raise ValueError("knowledge ingestion metadata is invalid")


def _chunk_sections(parsed: ParsedDocument, limits: ParserLimits) -> tuple[KnowledgeChunkInput, ...]:
    if not isinstance(parsed, ParsedDocument) or not isinstance(parsed.text_sections, tuple):
        raise DocumentParseError("DOCUMENT_MALFORMED", "parsed document result is invalid")
    chunks: list[KnowledgeChunkInput] = []
    for section in parsed.text_sections:
        if not isinstance(section, ParsedTextSection) or not isinstance(section.source_reference, Mapping):
            raise DocumentParseError("DOCUMENT_MALFORMED", "parsed document result is invalid")
        if not isinstance(section.text, str):
            raise DocumentParseError("DOCUMENT_MALFORMED", "parsed document result is invalid")
        text = normalize_text(section.text)
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(start + limits.max_chunk_characters, len(text))
            if end < len(text):
                boundary = max(text.rfind("\n", start + limits.max_chunk_characters // 2, end),
                               text.rfind(" ", start + limits.max_chunk_characters // 2, end))
                if boundary > start:
                    end = boundary
            content = text[start:end].strip()
            if content:
                if len(chunks) >= limits.max_chunks:
                    raise DocumentParseError("DOCUMENT_CHUNK_LIMIT_EXCEEDED", "document has too many chunks")
                source_reference = dict(section.source_reference)
                source_reference["start_char"] = start
                source_reference["end_char"] = end
                chunks.append(KnowledgeChunkInput(content=content, source_reference=source_reference))
            start = end
            while start < len(text) and text[start].isspace():
                start += 1
    if not chunks:
        raise DocumentParseError("DOCUMENT_TEXT_UNAVAILABLE", "document contains no extractable text")
    return tuple(chunks)


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
