from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class KnowledgeAuthorizationContext:
    """Server-computed grants; never construct this from an untrusted request body."""

    organization_id: str
    subject_id: str
    scopes: tuple[str, ...]
    classifications: tuple[str, ...]
    permissions: tuple[str, ...]
    knowledge_permissions: tuple[str, ...]
    privacy: str
    budget_ref: str
    trace_id: str


def authorization_allows_retrieval(
    context: KnowledgeAuthorizationContext,
) -> bool:
    """Fail closed unless every ACL dimension and model authorization field is present."""
    return all(
        (
            _non_empty(context.organization_id),
            _non_empty(context.subject_id),
            _non_empty(context.privacy),
            _non_empty(context.budget_ref),
            _non_empty(context.trace_id),
            _non_empty_values(context.scopes),
            _non_empty_values(context.classifications),
            _non_empty_values(context.permissions),
            _knowledge_permission_values(context.knowledge_permissions),
        )
    )


def authorized_document_predicate() -> str:
    """The fixed database ACL predicate used inside the materialized candidate CTE."""
    return """
        knowledge_documents.organization_id = :organization_id
        AND knowledge_documents.scope = ANY(:allowed_scopes)
        AND knowledge_documents.classification = ANY(:allowed_classifications)
        AND EXISTS (
            SELECT 1
            FROM knowledge_document_acl
            WHERE knowledge_document_acl.organization_id = knowledge_documents.organization_id
              AND knowledge_document_acl.document_id = knowledge_documents.document_id
              AND knowledge_document_acl.permission_code = ANY(:knowledge_permissions)
        )
    """


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _non_empty_values(values: object) -> bool:
    return (
        isinstance(values, tuple)
        and bool(values)
        and all(_non_empty(value) for value in values)
    )


def _knowledge_permission_values(values: object) -> bool:
    return _non_empty_values(values) and all(value.startswith("knowledge.") for value in values)
