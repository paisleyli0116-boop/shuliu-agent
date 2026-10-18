from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.approval.routes import PostgresApprovalRepository, create_approval_router
from app.identity.api_auth import current_user_dependency
from app.identity.provider import IdentityProvider
from app.identity.rbac import RBACRepository
from app.routes.approvals import create_approval_list_router
from app.routes.health import create_health_router
from app.routes.tasks import create_task_router
from app.workers.auth import WorkerCredentialService
from app.workers.coordinator import WorkerCoordinator
from app.workers.leases import LeaseConfig
from app.workers.routes import WebSocketConnectionManager, create_worker_router
from shuliu_runtime.approval.service import ApprovalService
from fastapi import FastAPI
from sqlalchemy.engine import Engine


def create_app(
    *,
    engine: Engine,
    session_secret_resolver: Callable[[], bytes],
    clock: Callable[[], datetime] | None = None,
    lease_config: LeaseConfig | None = None,
    identity_provider: IdentityProvider | None = None,
    rbac_repository: RBACRepository | None = None,
) -> FastAPI:
    """Build Cloud API with explicit database and Cloud Secret Manager dependencies."""
    now = clock or (lambda: datetime.now(UTC))
    worker_auth = WorkerCredentialService(
        engine=engine,
        session_secret_resolver=session_secret_resolver,
        clock=now,
    )
    connections = WebSocketConnectionManager()
    coordinator = WorkerCoordinator(
        engine=engine,
        transport=connections,
        config=lease_config
        or LeaseConfig(
            lease_duration=timedelta(seconds=30),
            heartbeat_interval=timedelta(seconds=10),
            grace_period=timedelta(seconds=30),
            max_attempts=3,
        ),
        clock=now,
    )
    app = FastAPI(title="ShuLiu Foundation Cloud API", version="0.1.0")
    app.state.worker_auth = worker_auth
    app.state.worker_coordinator = coordinator
    app.state.worker_connections = connections
    app.include_router(
        create_worker_router(
            auth=worker_auth, coordinator=coordinator, connections=connections
        )
    )
    rbac = rbac_repository or RBACRepository(engine)
    current_user = current_user_dependency(identity_provider, clock=now)
    approval_service = ApprovalService(
        repository=PostgresApprovalRepository(engine),
        authorize_approver=lambda **values: rbac.authorize(
            user_id=values["approver_id"],
            organization_id=values["organization_id"],
            permission=values["permission"],
            resource=values["resource"],
        ),
        approval_ttl=timedelta(hours=24),
        clock=now,
    )
    app.state.approval_service = approval_service
    app.include_router(create_task_router(engine=engine, rbac=rbac, current_user=current_user))
    app.include_router(
        create_approval_list_router(
            engine=engine, rbac=rbac, current_user=current_user
        )
    )
    app.include_router(create_approval_router(approval_service, current_user), prefix="/v1")
    app.include_router(create_health_router(engine))

    return app
