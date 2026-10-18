from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.engine import Engine


def create_health_router(engine: Engine) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health")
    def health() -> dict[str, object]:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 - health is intentionally fail-closed
            return {"status": "UNHEALTHY", "component": "cloud-api"}
        return {"status": "HEALTHY", "component": "cloud-api"}

    return router
