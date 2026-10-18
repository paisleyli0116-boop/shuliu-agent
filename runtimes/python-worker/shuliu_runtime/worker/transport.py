from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any
from uuid import uuid4

from shuliu_runtime.security.secret_store import SecretStore
from websockets.sync.client import ClientConnection, connect

from .credentials import (
    RegistrationGrant,
    WorkerAuthenticationError,
    WorkerDeviceKeyStore,
    WorkerSessionGrant,
    session_proof_bytes,
)
from .protocol import WorkerMessage, WorkerProtocolError


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class HttpWorkerRegistrationCloud:
    """HTTPS adapter for the Foundation Worker enrollment and session APIs."""

    def __init__(
        self,
        *,
        api_base_url: str,
        ssl_context: ssl.SSLContext | None = None,
        timeout_seconds: float = 10,
    ) -> None:
        if not api_base_url.startswith("https://"):
            raise ValueError("Worker registration requires HTTPS")
        self._base_url = api_base_url.rstrip("/")
        self._ssl_context = ssl_context or ssl.create_default_context()
        self._timeout = timeout_seconds

    def register(
        self,
        registration_token: str,
        device_public_key: bytes,
        device_signature: bytes,
        *,
        device_key_algorithm: str,
        worker_version: str,
        capabilities: tuple[str, ...],
    ) -> RegistrationGrant:
        response = self._post(
            "/v1/workers/registrations",
            {
                "registration_token": registration_token,
                "worker_version": worker_version,
                "protocol_version": 1,
                "capabilities": list(capabilities),
                "device_key_algorithm": device_key_algorithm,
                "device_public_key": _b64(device_public_key),
                "device_signature": _b64(device_signature),
            },
        )
        try:
            return RegistrationGrant(
                worker_id=response["worker_id"],
                session_credential=response["session_credential"],
                expires_at=_timestamp(response["expires_at"]),
            )
        except (KeyError, TypeError, ValueError):
            raise WorkerAuthenticationError(
                "WORKER_REGISTRATION_RESPONSE_INVALID",
                "Cloud returned an invalid Worker registration response",
            ) from None

    def renew_session(
        self, *, worker_id: str, device_key_ref: str, secret_store: SecretStore
    ) -> WorkerSessionGrant:
        challenge = self._post(
            "/v1/workers/session-challenges", {"worker_id": worker_id}
        )
        try:
            challenge_id = challenge["challenge_id"]
            nonce = challenge["nonce"]
            device_keys = WorkerDeviceKeyStore(secret_store)
            signature = device_keys.sign(
                device_key_ref,
                session_proof_bytes(
                    worker_id=worker_id,
                    challenge_id=challenge_id,
                    nonce=nonce,
                ),
            )
            result = self._post(
                "/v1/workers/sessions",
                {
                    "worker_id": worker_id,
                    "challenge_id": challenge_id,
                    "device_signature": _b64(signature),
                },
            )
            return WorkerSessionGrant(
                session_credential=result["session_credential"],
                expires_at=_timestamp(result["expires_at"]),
            )
        except (KeyError, TypeError, ValueError):
            raise WorkerAuthenticationError(
                "WORKER_SESSION_RESPONSE_INVALID",
                "Cloud returned an invalid Worker session response",
            ) from None

    def connect(
        self,
        *,
        websocket_url: str,
        worker_id: str,
        session_credential: str,
        ssl_context: ssl.SSLContext | None = None,
        timeout_seconds: float = 10,
    ) -> WorkerWebSocketConnection:
        return WorkerWebSocketConnection(
            websocket_url=websocket_url,
            worker_id=worker_id,
            session_credential=session_credential,
            ssl_context=ssl_context or self._ssl_context,
            timeout_seconds=timeout_seconds,
        )

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, context=self._ssl_context, timeout=self._timeout
            ) as response:
                decoded = json.loads(response.read())
        except urllib.error.HTTPError as error:
            try:
                body = json.loads(error.read())
                code = body.get("detail", {}).get("code", "WORKER_API_REJECTED")
            except Exception:  # noqa: BLE001 - do not propagate response bodies
                code = "WORKER_API_REJECTED"
            raise WorkerAuthenticationError(code, "Cloud rejected the Worker API request") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            # Registration may have consumed its one-time token before the response was lost.
            raise
        if not isinstance(decoded, dict):
            raise WorkerAuthenticationError(
                "WORKER_API_RESPONSE_INVALID", "Cloud returned an invalid Worker API response"
            )
        return decoded


class WorkerWebSocketConnection:
    def __init__(
        self,
        *,
        websocket_url: str,
        worker_id: str,
        session_credential: str,
        ssl_context: ssl.SSLContext,
        timeout_seconds: float,
    ) -> None:
        if not websocket_url.startswith("wss://"):
            raise ValueError("Worker connection requires WSS")
        self._url = websocket_url
        self._worker_id = worker_id
        self._credential = session_credential
        self._ssl_context = ssl_context
        self._timeout = timeout_seconds
        self._socket: ClientConnection | None = None
        self._connection_context = None

    def __enter__(self) -> WorkerWebSocketConnection:
        self._connection_context = connect(
            self._url,
            ssl=self._ssl_context,
            open_timeout=self._timeout,
        )
        self._socket = self._connection_context.__enter__()
        response = self.send(
            "authenticate", {"session_credential": self._credential}
        )
        if (
            response.message_type != "authenticated"
            or response.payload.get("worker_id") != self._worker_id
        ):
            self.close()
            raise WorkerAuthenticationError(
                "WORKER_IDENTITY_MISMATCH", "Cloud authenticated a different Worker identity"
            )
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def send(self, message_type: str, payload: dict[str, Any]) -> WorkerMessage:
        if self._socket is None:
            raise RuntimeError("Worker WebSocket is not connected")
        message = WorkerMessage(
            message_id=str(uuid4()),
            request_id=str(uuid4()),
            trace_id=str(uuid4()),
            message_type=message_type,
            payload=payload,
        )
        self._socket.send(json.dumps(message.as_dict(), separators=(",", ":")))
        try:
            response = WorkerMessage.parse(json.loads(self._socket.recv(timeout=self._timeout)))
        except (WorkerProtocolError, json.JSONDecodeError, TypeError):
            raise WorkerAuthenticationError(
                "WORKER_PROTOCOL_RESPONSE_INVALID", "Cloud returned an invalid Worker message"
            ) from None
        if response.message_type == "error":
            raise WorkerAuthenticationError(
                str(response.payload.get("code", "WORKER_REQUEST_REJECTED")),
                "Cloud rejected the Worker request",
            )
        return response

    def close(self) -> None:
        if self._connection_context is not None:
            self._connection_context.__exit__(None, None, None)
            self._connection_context = None
            self._socket = None
