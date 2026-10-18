from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class KnowledgeResult:
    chunk_id: str
    document_id: str
    content: str
    source_reference: Mapping[str, object]
    distance: float


class KnowledgeTransport(Protocol):
    def retrieve(self, query: str, top_k: int) -> Sequence[KnowledgeResult]: ...


class KnowledgeClient:
    """Thin remote client that delegates authorization and vector search to Cloud."""

    def __init__(self, transport: KnowledgeTransport) -> None:
        self._transport = transport

    def retrieve(self, query: str, top_k: int = 5) -> tuple[KnowledgeResult, ...]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("knowledge query is required")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 100:
            raise ValueError("knowledge top_k must be between 1 and 100")
        results = self._transport.retrieve(query, top_k)
        if not isinstance(results, Sequence):
            raise ValueError("knowledge transport response is invalid")
        return tuple(results)
