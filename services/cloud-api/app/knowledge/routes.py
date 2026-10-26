from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .audit import DENIED, VERIFIED, KnowledgeAuditSink, KnowledgeAuditRecord
from .dependencies import (
    KnowledgeAuthorizationDenied,
    KnowledgeMembershipPort,
    KnowledgeRBACPort,
    build_knowledge_context,
)
from .retrieval import KnowledgeRetriever


class KnowledgeSearchRequest(BaseModel):
    """Only query parameters are accepted; every authorization field is forbidden."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=100)


def create_knowledge_router(
    *,
    membership_port: KnowledgeMembershipPort,
    rbac_port: KnowledgeRBACPort,
    retriever: KnowledgeRetriever,
    audit_sink: KnowledgeAuditSink,
    policy_version: str,
    current_user: object,
) -> APIRouter:
    router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])

    @router.post("/search")
    def search(
        request: KnowledgeSearchRequest, user_id: str = Depends(current_user)  # type: ignore[arg-type]
    ) -> dict[str, object]:
        trace_id = str(uuid4())

        def denied_record(reason: str) -> KnowledgeAuditRecord:
            snapshot = membership_port.current_membership(user_id)
            return KnowledgeAuditRecord(
                organization_id=(
                    snapshot.organization_id if snapshot is not None else ""
                ),
                subject_id=user_id,
                trace_id=trace_id,
                outcome=DENIED,
                policy_version=policy_version,
                reason=reason,
            )

        try:
            snapshot = membership_port.current_membership(user_id)
            context = build_knowledge_context(
                user_id, snapshot, rbac_port, trace_id=trace_id
            )
        except KnowledgeAuthorizationDenied as authorization_error:
            try:
                audit_sink.append(
                    denied_record(authorization_error.reason)
                )
            except Exception:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "KNOWLEDGE_AUDIT_UNAVAILABLE",
                        "trace_id": trace_id,
                    },
                ) from None
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "KNOWLEDGE_AUTHORIZATION_DENIED",
                    "trace_id": trace_id,
                },
            ) from None
        try:
            hits = retriever.retrieve(request.query, context, request.top_k)
        except Exception:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "KNOWLEDGE_RETRIEVAL_UNAVAILABLE",
                    "trace_id": trace_id,
                },
            ) from None
        try:
            audit_sink.append(
                KnowledgeAuditRecord(
                    organization_id=context.organization_id,
                    subject_id=context.subject_id,
                    trace_id=trace_id,
                    outcome=VERIFIED,
                    policy_version=policy_version,
                    document_refs=tuple(
                        {
                            "document_id": hit.document_id,
                            "chunk_id": hit.chunk_id,
                        }
                        for hit in hits
                    ),
                )
            )
        except Exception:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "KNOWLEDGE_AUDIT_UNAVAILABLE",
                    "trace_id": trace_id,
                },
            ) from None
        return {
            "trace_id": trace_id,
            "policy_version": policy_version,
            "hits": [
                {
                    "chunk_id": hit.chunk_id,
                    "document_id": hit.document_id,
                    "content": hit.content,
                    "source_reference": dict(hit.source_reference),
                    "distance": hit.distance,
                }
                for hit in hits
            ],
        }

    return router
