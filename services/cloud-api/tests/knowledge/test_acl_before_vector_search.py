from __future__ import annotations

import importlib
import importlib.util

from sqlalchemy.dialects import postgresql
from sqlalchemy import insert


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

    statement = module.build_retrieval_statement(context, (0.1, 0.2), top_k=5)
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
    assert "authorized_chunks.embedding <=>" in sql[cte_end:ranking_start]
    assert ranking_start > cte_end


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
