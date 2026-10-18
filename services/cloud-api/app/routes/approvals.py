from __future__ import annotations

from collections.abc import Callable

from app.identity.rbac import RBACRepository
from app.persistence.postgres import approvals
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.engine import Engine


def create_approval_list_router(
    *, engine: Engine, rbac: RBACRepository, current_user: Callable[..., str]
) -> APIRouter:
    router = APIRouter(prefix="/v1/approvals", tags=["approvals"])

    @router.get("")
    def list_approvals(
        user_id: str = Depends(current_user),
        organization_id: str = Header(alias="X-Organization-ID"),
    ) -> dict[str, object]:
        authorized = rbac.authorize(
            user_id=user_id,
            organization_id=organization_id,
            permission="foundation.approval.read",
            resource={"organization_id": organization_id, "resource_type": "approval"},
        )
        if not authorized:
            raise HTTPException(status_code=403, detail={"code": "PERMISSION_DENIED"})
        with engine.connect() as connection:
            rows = connection.execute(
                select(
                    approvals.c.approval_id,
                    approvals.c.status,
                    approvals.c.action_digest,
                    approvals.c.created_at,
                )
                .where(approvals.c.organization_id == organization_id)
                .order_by(approvals.c.created_at.desc())
            ).mappings().all()
        return {
            "items": [
                {
                    "approval_id": row["approval_id"],
                    "status": row["status"],
                    "action_digest": row["action_digest"],
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ]
        }

    return router
