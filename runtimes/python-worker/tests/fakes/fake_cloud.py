from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Callable
from uuid import uuid4

from shuliu_runtime.worker.credentials import (
    RegistrationGrant,
    WorkerAuthenticationError,
)


@dataclass(frozen=True)
class FakeWorkerRecord:
    worker_id: str
    device_public_key: bytes
    device_key_algorithm: str
    worker_version: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class WorkerSession:
    worker_id: str
    expires_at: datetime


class FakeCloud:
    """Test-only Cloud registration endpoint with atomic one-time token consumption."""

    def __init__(
        self,
        *,
        session_signing_key: bytes,
        session_ttl: timedelta,
        clock: Callable[[], datetime],
    ) -> None:
        if len(session_signing_key) < 32:
            raise ValueError("test Cloud signing key must contain at least 32 bytes")
        if session_ttl <= timedelta(0):
            raise ValueError("test session TTL must be positive")
        self._now = clock().astimezone(timezone.utc)
        self._session_signing_key = session_signing_key
        self._session_ttl = session_ttl
        self._registration_tokens: dict[str, datetime] = {}
        self._consumed_tokens: set[str] = set()
        self._lock = Lock()
        self.registered_workers: dict[str, FakeWorkerRecord] = {}

    def _current_time(self) -> datetime:
        return self._now

    def set_time(self, value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fake clock must be timezone-aware")
        self._now = value.astimezone(timezone.utc)

    def issue_registration_token(self, *, expires_at: datetime) -> str:
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("registration token expiry must be timezone-aware")
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._lock:
            self._registration_tokens[digest] = expires_at.astimezone(timezone.utc)
        return token

    def register(
        self,
        registration_token: str,
        device_public_key: bytes,
        *,
        device_key_algorithm: str,
        worker_version: str,
        capabilities: tuple[str, ...],
    ) -> RegistrationGrant:
        if device_key_algorithm != "Ed25519":
            raise WorkerAuthenticationError(
                "WORKER_DEVICE_KEY_INVALID", "worker device key is invalid"
            )
        if not isinstance(device_public_key, bytes) or len(device_public_key) != 32:
            raise WorkerAuthenticationError(
                "WORKER_DEVICE_KEY_INVALID", "worker device key is invalid"
            )
        if not worker_version.strip() or any(not capability.strip() for capability in capabilities):
            raise WorkerAuthenticationError(
                "WORKER_REGISTRATION_METADATA_INVALID", "worker registration metadata is invalid"
            )
        digest = hashlib.sha256(registration_token.encode("utf-8")).hexdigest()
        now = self._current_time()
        with self._lock:
            expiry = self._registration_tokens.get(digest)
            if digest in self._consumed_tokens:
                raise WorkerAuthenticationError(
                    "WORKER_REGISTRATION_TOKEN_USED", "worker registration token was already used"
                )
            if expiry is None:
                raise WorkerAuthenticationError(
                    "WORKER_REGISTRATION_TOKEN_INVALID", "worker registration token is invalid"
                )
            if now >= expiry:
                self._consumed_tokens.add(digest)
                raise WorkerAuthenticationError(
                    "WORKER_REGISTRATION_TOKEN_EXPIRED", "worker registration token has expired"
                )
            self._consumed_tokens.add(digest)
            worker_id = f"worker_{uuid4().hex}"
            expires_at = now + self._session_ttl
            payload = {
                "exp": expires_at.timestamp(),
                "jti": secrets.token_urlsafe(16),
                "worker_id": worker_id,
            }
            encoded_payload = _b64url_encode(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            signing_input = f"v1.{encoded_payload}".encode("ascii")
            signature = hmac.new(self._session_signing_key, signing_input, hashlib.sha256).digest()
            credential = f"v1.{encoded_payload}.{_b64url_encode(signature)}"
            grant = RegistrationGrant(worker_id, credential, expires_at)
            self.registered_workers[worker_id] = FakeWorkerRecord(
                worker_id, device_public_key, device_key_algorithm, worker_version, capabilities
            )
            return grant

    def authenticate(self, session_credential: str) -> WorkerSession:
        try:
            version, encoded_payload, encoded_signature = session_credential.split(".")
            if version != "v1":
                raise ValueError
            signature = _b64url_decode(encoded_signature)
            payload = json.loads(_b64url_decode(encoded_payload))
        except (AttributeError, ValueError, UnicodeError, json.JSONDecodeError):
            raise WorkerAuthenticationError(
                "WORKER_SESSION_INVALID", "worker session credential is invalid"
            ) from None
        expected = hmac.new(
            self._session_signing_key,
            f"{version}.{encoded_payload}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected, signature):
            raise WorkerAuthenticationError(
                "WORKER_SESSION_SIGNATURE_INVALID", "worker session signature is invalid"
            )
        try:
            worker_id = payload["worker_id"]
            expires_at = datetime.fromtimestamp(payload["exp"], timezone.utc)
            if not isinstance(worker_id, str) or not isinstance(payload["jti"], str):
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError):
            raise WorkerAuthenticationError(
                "WORKER_SESSION_INVALID", "worker session credential is invalid"
            ) from None
        if self._current_time() >= expires_at:
            raise WorkerAuthenticationError(
                "WORKER_SESSION_EXPIRED", "worker session credential has expired"
            )
        session = WorkerSession(worker_id, expires_at)
        if session.worker_id not in self.registered_workers:
            raise WorkerAuthenticationError(
                "WORKER_SESSION_INVALID", "worker session credential is invalid"
            )
        return session


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    if not value or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for char in value):
        raise ValueError("invalid base64url value")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
