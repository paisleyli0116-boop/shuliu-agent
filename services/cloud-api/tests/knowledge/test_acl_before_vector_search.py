from __future__ import annotations

import importlib
import importlib.util

from sqlalchemy.dialects import postgresql
from sqlalchemy import insert
import pytest
from app.knowledge.embedding import EmbeddingProfile


def test_acl_authorized_candidates_are_materialized_before_vector_ranking() -> None:
    module_name = "app.knowledge.retrieval"
    assert importlib.util.find_spec(module_name) is not None, (
        "Knowledge retrieval query builder is not implemented"
    )
    module = importlib.import_module(module_name)
    context = module.KnowledgeAuthorizationContext(
        organization_id="org-a",
        subject_id="user-a",
        scopes=("team:finance",),
        classifications=("INTERNAL",),
        permissions=("foundation.model.invoke",),
        knowledge_permissions=("knowledge.read",),
        privacy="INTERNAL",
        budget_ref="budget://org-a/knowledge",
        trace_id="trace-knowledge-1",
    )

    profile = EmbeddingProfile(
        "knowledge-default-v1",
        "embed-provider",
        "embed-model-v1",
        2,
        frozenset({"INTERNAL"}),
        1,
    )
    statement = module.build_retrieval_statement(context, profile, (0.1, 0.2), top_k=5)
    sql = str(statement.compile(dialect=postgresql.dialect()))
    cte_end = sql.index(")\n        SELECT authorized_chunks.chunk_id")
    ranking_start = sql.index("ORDER BY")

    assert "WITH authorized_chunks AS MATERIALIZED" in sql
    assert "knowledge_documents.organization_id" in sql[:cte_end]
    assert "knowledge_documents.scope" in sql[:cte_end]
    assert "knowledge_documents.classification" in sql[:cte_end]
    assert "knowledge_document_acl" in sql[:cte_end]
    assert "knowledge_document_acl.permission_code = ANY" in sql[:cte_end]
    assert statement.compile(dialect=postgresql.dialect()).params["knowledge_permissions"] == ["knowledge.read"]
    params = statement.compile(dialect=postgresql.dialect()).params
    assert params["embedding_profile_id"] == "knowledge-default-v1"
    assert params["embedding_provider_id"] == "embed-provider"
    assert params["embedding_model"] == "embed-model-v1"
    assert params["embedding_dimensions"] == 2
    assert params["embedding_index_version"] == 1
    assert "embedding_profile_id =" in sql[:cte_end]
    assert "knowledge_embedding_profiles.state = 'READY'" in sql[:cte_end]
    assert "AND NOT EXISTS" in sql[:cte_end]
    assert "authorized_chunks.embedding <=>" in sql[cte_end:ranking_start]
    assert ranking_start > cte_end


def test_retrieval_rejects_vector_dimension_mismatch_before_query_build() -> None:
    from app.knowledge.retrieval import build_retrieval_statement
    from app.knowledge.acl import KnowledgeAuthorizationContext

    context = KnowledgeAuthorizationContext(
        organization_id="org-a",
        subject_id="user-a",
        scopes=("team:finance",),
        classifications=("INTERNAL",),
        permissions=("foundation.model.invoke",),
        knowledge_permissions=("knowledge.read",),
        privacy="INTERNAL",
        budget_ref="budget://org-a/knowledge",
        trace_id="trace-knowledge-dimensions",
    )
    profile = EmbeddingProfile(
        "knowledge-default-v1", "embed-provider", "embed-model-v1", 3,
        frozenset({"INTERNAL"}), 1,
    )

    with pytest.raises(ValueError, match="dimensions"):
        build_retrieval_statement(context, profile, (0.1, 0.2), top_k=5)


def test_missing_permission_context_fails_closed_before_query_build() -> None:
    module_name = "app.knowledge.retrieval"
    assert importlib.util.find_spec(module_name) is not None, (
        "Knowledge retrieval query builder is not implemented"
    )
    module = importlib.import_module(module_name)
    context = module.KnowledgeAuthorizationContext(
        organization_id="org-a",
        subject_id="user-a",
        scopes=("team:finance",),
        classifications=("INTERNAL",),
        permissions=("foundation.model.invoke",),
        knowledge_permissions=(),
        privacy="INTERNAL",
        budget_ref="budget://org-a/knowledge",
        trace_id="trace-knowledge-2",
    )

    assert module.authorization_allows_retrieval(context) is False


def test_pgvector_insert_explicitly_casts_embedding_bind_to_vector() -> None:
    from app.persistence.postgres import knowledge_chunks

    statement = insert(knowledge_chunks).values(
        chunk_id="doc-1:0",
        organization_id="org-a",
        document_id="doc-1",
        chunk_index=0,
        content="authorized text",
        source_reference={"page": 1},
        embedding=(0.1, 0.2),
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "CAST(" in sql
    assert "AS vector)" in sql
