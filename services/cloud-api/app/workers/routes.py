from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.workers.auth import WorkerAuthenticationError, WorkerCredentialService
from app.workers.coordinator import WorkerCoordinator
from app.workers.leases import WorkerCoordinatorError, WorkerPrincipal
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field
from shuliu_runtime.worker.protocol import WorkerMessage, WorkerProtocolError


class RegistrationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registration_token: str = Field(min_length=1)
    worker_version: str = Field(min_length=1, max_length=64)
    protocol_version: int = Field(ge=1, le=1)
    capabilities: list[str]
    device_key_algorithm: str
    device_public_key: str
    device_signature: str


class SessionChallengeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1, max_length=128)


class SessionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1, max_length=128)
    challenge_id: str = Field(min_length=1, max_length=64)
    device_signature: str = Field(min_length=1)


def _envelope(message_type: str, payload: dict[str, Any], *, trace_id: str | None = None) -> dict[str, Any]:
    return {
        "protocol_name": "worker",
        "protocol_version": 1,
        "message_id": str(uuid4()),
        "request_id": str(uuid4()),
        "trace_id": trace_id or str(uuid4()),
        "message_type": message_type,
        "payload": payload,
    }


class WebSocketConnectionManager:
    """A single active connection queue per authenticated Worker identity."""

    def __init__(self) -> None:
        self._connections: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = {}

    def connect(self, worker_id: str) -> asyncio.Queue:
        if worker_id in self._connections:
            raise WorkerAuthenticationError(
                "WORKER_ALREADY_CONNECTED", "worker already has an active connection"
            )
        queue: asyncio.Queue = asyncio.Queue()
        self._connections[worker_id] = (asyncio.get_running_loop(), queue)
        return queue

    def disconnect(self, worker_id: str) -> None:
        self._connections.pop(worker_id, None)

    def send(self, worker_id: str, message: dict[str, Any]) -> None:
        connection = self._connections.get(worker_id)
        if connection is None:
            raise OSError("authenticated Worker is not connected")
        loop, queue = connection
        if loop.is_closed():
            raise OSError("Worker event loop is closed")
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is loop:
            queue.put_nowait(message)
        else:
            asyncio.run_coroutine_threadsafe(queue.put(message), loop).result(timeout=2)


def create_worker_router(
    *, auth: WorkerCredentialService, coordinator: WorkerCoordinator, connections: WebSocketConnectionManager
) -> APIRouter:
    router = APIRouter(prefix="/v1/workers", tags=["workers"])

    @router.post("/registrations", status_code=201)
    def register_worker(body: RegistrationBody) -> dict[str, object]:
        try:
            registered = auth.register(body.model_dump())
        except WorkerAuthenticationError as error:
            raise HTTPException(status_code=401, detail={"code": error.code}) from error
        return {
            "worker_id": registered.worker_id,
            "session_credential": registered.credential,
            "expires_at": registered.expires_at.isoformat(),
        }

    @router.post("/session-challenges", status_code=201)
    def create_session_challenge(body: SessionChallengeBody) -> dict[str, object]:
        try:
            return auth.issue_challenge(worker_id=body.worker_id)
        except WorkerAuthenticationError as error:
            raise HTTPException(status_code=401, detail={"code": error.code}) from error

    @router.post("/sessions", status_code=201)
    def create_session(body: SessionBody) -> dict[str, object]:
        try:
            session = auth.create_session(
                worker_id=body.worker_id,
                challenge_id=body.challenge_id,
                device_signature=body.device_signature,
            )
        except WorkerAuthenticationError as error:
            raise HTTPException(status_code=401, detail={"code": error.code}) from error
        return {"session_credential": session.credential, "expires_at": session.expires_at.isoformat()}

    @router.websocket("/ws")
    async def worker_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        principal: WorkerPrincipal | None = None
        queue: asyncio.Queue | None = None
        connected = False
        try:
            first = WorkerMessage.parse(await websocket.receive_json())
            if first.message_type != "authenticate":
                raise WorkerAuthenticationError(
                    "WORKER_AUTHENTICATION_REQUIRED", "Worker authentication is required"
                )
            credential = first.payload.get("session_credential")
            if not isinstance(credential, str):
                raise WorkerAuthenticationError(
                    "WORKER_SESSION_INVALID", "worker session credential is invalid"
                )
            worker_id, organization_id, _ = auth.authenticate_session(credential)
            principal = WorkerPrincipal(worker_id=worker_id, organization_id=organization_id)
            metadata = auth.get_worker_metadata(
                worker_id=worker_id, organization_id=organization_id
            )
            queue = connections.connect(worker_id)
            connected = True
            coordinator.record_authenticated_worker(
                principal,
                worker_version=metadata["worker_version"],
                protocol_version=metadata["protocol_version"],
                capabilities=tuple(metadata["capabilities"]),
            )
            coordinator.heartbeat(principal)
            await websocket.send_json(
                _envelope(
                    "authenticated",
                    {
                        "worker_id": principal.worker_id,
                        "organization_id": principal.organization_id,
                    },
                    trace_id=first.trace_id,
                )
            )

            while True:
                incoming = WorkerMessage.parse(await websocket.receive_json())
                payload = incoming.payload
                try:
                    if incoming.message_type == "heartbeat":
                        receipt = coordinator.heartbeat(principal)
                        await websocket.send_json(
                            _envelope(
                                "heartbeat",
                                {"worker_id": principal.worker_id, "sent_at": receipt.received_at.isoformat()},
                                trace_id=incoming.trace_id,
                            )
                        )
                    elif incoming.message_type == "lease_request":
                        task_id = payload.get("task_id")
                        key = payload.get("idempotency_key")
                        if not isinstance(task_id, str) or not isinstance(key, str):
                            await websocket.send_json(_error_message("WORKER_LEASE_REQUEST_INVALID", incoming))
                            continue
                        coordinator.lease(
                            task_id,
                            principal,
                            idempotency_key=key,
                            trace_id=incoming.trace_id,
                        )
                    elif incoming.message_type == "lease_authorize":
                        task_id = payload.get("task_id")
                        lease_id = payload.get("lease_id")
                        fencing_token = payload.get("fencing_token")
                        key = payload.get("idempotency_key")
                        action_digest = payload.get("action_digest")
                        if (
                            not isinstance(task_id, str)
                            or not isinstance(lease_id, str)
                            or isinstance(fencing_token, bool)
                            or not isinstance(fencing_token, int)
                            or not isinstance(key, str)
                        ):
                            await websocket.send_json(_error_message("WORKER_LEASE_REQUEST_INVALID", incoming))
                            continue
                        if not isinstance(action_digest, str):
                            await websocket.send_json(
                                _error_message("WORKER_LEASE_ACTION_BINDING_REQUIRED", incoming)
                            )
                            continue
                        permit = coordinator.authorize_tool_dispatch(
                            principal,
                            task_id=task_id,
                            lease_id=lease_id,
                            fencing_token=fencing_token,
                            idempotency_key=key,
                            action_digest=action_digest,
                        )
                        await websocket.send_json(
                            _envelope(
                                "lease_authorized",
                                {
                                    "task_id": permit.task_id,
                                    "lease_id": permit.lease_id,
                                    "fencing_token": permit.fencing_token,
                                    "idempotency_key": permit.idempotency_key,
                                    "action_digest": permit.action_digest,
                                    "expires_at": permit.expires_at.isoformat(),
                                },
                                trace_id=incoming.trace_id,
                            )
                        )
                    elif incoming.message_type == "result":
                        coordinator.commit_result(
                            principal,
                            lease_id=str(payload["lease_id"]),
                            fencing_token=int(payload["fencing_token"]),
                            idempotency_key=str(payload["idempotency_key"]),
                            result={"status": payload["status"], "result": payload["result"]},
                            trace_id=incoming.trace_id,
                        )
                    else:
                        await websocket.send_json(
                            _error_message("WORKER_MESSAGE_UNSUPPORTED", incoming)
                        )
                except WorkerCoordinatorError as error:
                    await websocket.send_json(_error_message(error.code, incoming))
                while not queue.empty():
                    await websocket.send_json(queue.get_nowait())
        except WebSocketDisconnect:
            pass
        except (WorkerAuthenticationError, WorkerProtocolError) as error:
            code = getattr(error, "code", "WORKER_PROTOCOL_INVALID")
            await websocket.close(code=4401 if isinstance(error, WorkerAuthenticationError) else 4400, reason=code)
        finally:
            if principal is not None and connected:
                connections.disconnect(principal.worker_id)
                try:
                    coordinator.disconnected(principal)
                except WorkerCoordinatorError:
                    pass

    return router


def _error_message(code: str, incoming: WorkerMessage | None) -> dict[str, Any]:
    return _envelope(
        "error",
        {
            "task_id": str(incoming.payload.get("task_id", "unknown")) if incoming else "unknown",
            "lease_id": str(incoming.payload.get("lease_id", "unknown")) if incoming else "unknown",
            "code": code,
            "retryable": False,
        },
        trace_id=incoming.trace_id if incoming else None,
    )
