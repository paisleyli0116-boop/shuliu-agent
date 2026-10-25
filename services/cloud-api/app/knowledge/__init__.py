"""Cloud-side Knowledge ingestion and ACL-first retrieval."""

from .acl import KnowledgeAuthorizationContext
from .embedding import (
    EmbeddingConfigurationError,
    EmbeddingIndexNotReadyError,
    EmbeddingProfile,
    GatewayEmbeddingAdapter,
    load_embedding_profile,
)
from .ingest import KnowledgeChunkInput, KnowledgeDocumentInput, ingest_document
from .retrieval import KnowledgeHit, KnowledgeRetriever

__all__ = [
    "KnowledgeAuthorizationContext",
    "EmbeddingConfigurationError",
    "EmbeddingIndexNotReadyError",
    "EmbeddingProfile",
    "GatewayEmbeddingAdapter",
    "load_embedding_profile",
    "KnowledgeChunkInput",
    "KnowledgeDocumentInput",
    "KnowledgeHit",
    "KnowledgeRetriever",
    "ingest_document",
]
