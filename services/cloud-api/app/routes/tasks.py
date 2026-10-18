from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.identity.rbac import RBACRepository
from app.persistence.postgres import events, tasks
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine


class CreateTaskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: dict[str, Any] = Field(default_factory=dict)


def create_task_router(
    *,
    engine: Engine,
    rbac: RBACRepository,
    current_user: Callable[..., str],
) -> APIRouter:
    router = APIRouter(prefix="/v1/tasks", tags=["tasks"])

    def authorize(user_id: str, organization_id: str, permission: str) -> None:
        if not rbac.authorize(
            user_id=user_id,
            organization_id=organization_id,
            permission=permission,
            resource={"organization_id": organization_id, "resource_type": "task"},
        ):
            raise HTTPException(status_code=403, detail={"code": "PERMISSION_DENIED"})

    @router.post("", status_code=201)
    def create_task(
        body: CreateTaskBody,
        user_id: str = Depends(current_user),
        organization_id: str = Header(alias="X-Organization-ID"),
    ) -> dict[str, object]:
        authorize(user_id, organization_id, "foundation.task.create")
        now = datetime.now(UTC)
        task_id = f"task_{uuid4().hex}"
        trace_id = str(uuid4())
        event_id = str(uuid4())
        with engine.begin() as connection:
            connection.execute(
                insert(tasks).values(
                    task_id=task_id,
                    organization_id=organization_id,
                    status="READY",
                    payload=dict(body.payload),
                    created_at=now,
                )
            )
            connection.execute(
                insert(events).values(
                    event_id=event_id,
                    organization_id=organization_id,
                    idempotency_key=f"task:{task_id}:created",
                    payload={
                        "schema_version": 1,
                        "event_id": event_id,
                        "event_type": "TASK_CREATED",
                        "timestamp": now.isoformat(),
                        "organization_id": organization_id,
                        "trace_id": trace_id,
                        "payload": {"task_id": task_id, "created_by": user_id},
                    },
                    received_at=now,
                )
            )
        return {"task_id": task_id, "organization_id": organization_id, "status": "READY"}

    @router.get("/{task_id}")
    def get_task(
        task_id: str,
        user_id: str = Depends(current_user),
        organization_id: str = Header(alias="X-Organization-ID"),
    ) -> dict[str, object]:
        authorize(user_id, organization_id, "foundation.task.read")
        with engine.connect() as connection:
            row = connection.execute(
                select(tasks).where(
                    tasks.c.task_id == task_id,
                    tasks.c.organization_id == organization_id,
                )
            ).mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND"})
        return {
            "task_id": row["task_id"],
            "organization_id": row["organization_id"],
            "status": row["status"],
            "payload": dict(row["payload"]),
            "created_at": row["created_at"].isoformat(),
        }

    return router
