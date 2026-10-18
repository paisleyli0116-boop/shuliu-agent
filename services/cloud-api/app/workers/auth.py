from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.persistence.postgres import (
    worker_auth_challenges,
    worker_registration_tokens,
    worker_session_tokens,
    workers,
)
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from shuliu_runtime.worker.credentials import (
    registration_proof_bytes,
    session_proof_bytes,
)
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine


class WorkerAuthenticationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkerSession:
    worker_id: str
    organization_id: str
    credential: str
    expires_at: datetime


@dataclass(frozen=True)
class WorkerRegistration:
    worker_id: str
    organization_id: str
    worker_version: str
    protocol_version: int
    capabilities: tuple[str, ...]
    device_key_algorithm: str
    device_public_key: bytes
    credential: str
    expires_at: datetime


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not isinstance(value, str) or not value or any(
        item not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        for item in value
    ):
        raise ValueError("invalid base64url")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class WorkerCredentialService:
    """Cloud-side enrollment and one-use Worker session authentication.

    The session signing key is resolved on demand through the injected Cloud Secret
    Manager adapter. The service never accepts a signing key from an API request.
    """

    def __init__(
        self,
        *,
        engine: Engine,
        session_secret_resolver: Callable[[], bytes],
        clock: Callable[[], datetime] | None = None,
        session_ttl: timedelta = timedelta(minutes=5),
        registration_ttl: timedelta = timedelta(minutes=15),
        challenge_ttl: timedelta = timedelta(minutes=2),
    ) -> None:
        for name, value in (
            ("session_ttl", session_ttl),
            ("registration_ttl", registration_ttl),
            ("challenge_ttl", challenge_ttl),
        ):
            if value <= timedelta(0):
                raise ValueError(f"{name} must be positive")
        self._engine = engine
        self._resolve_secret = session_secret_resolver
        self._clock = clock or (lambda: datetime.now(UTC))
        self._session_ttl = session_ttl
        self._registration_ttl = registration_ttl
        self._challenge_ttl = challenge_ttl

    def issue_registration_token(
        self, *, organization_id: str, expires_at: datetime | None = None
    ) -> str:
        if not isinstance(organization_id, str) or not organization_id.strip():
            raise ValueError("organization_id is required")
        now = _aware(self._clock())
        expiry = _aware(expires_at) if expires_at else now + self._registration_ttl
        if expiry <= now:
            raise ValueError("registration token expiry must be in the future")
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._engine.begin() as connection:
            connection.execute(
                insert(worker_registration_tokens).values(
                    token_digest=digest,
                    organization_id=organization_id,
                    expires_at=expiry,
                    created_at=now,
                )
            )
        return token

    def register(self, payload: Mapping[str, object]) -> WorkerRegistration:
        registration_token = payload.get("registration_token")
        worker_version = payload.get("worker_version")
        protocol_version = payload.get("protocol_version")
        capabilities = payload.get("capabilities")
        algorithm = payload.get("device_key_algorithm")
        public_key_text = payload.get("device_public_key")
        signature_text = payload.get("device_signature")
        if (
            not isinstance(registration_token, str)
            or not registration_token
            or not isinstance(worker_version, str)
            or not worker_version.strip()
            or len(worker_version) > 64
            or type(protocol_version) is not int
            or protocol_version != 1
            or not isinstance(capabilities, list)
            or any(not isinstance(item, str) or not item.strip() for item in capabilities)
            or len(set(capabilities)) != len(capabilities)
            or algorithm != "Ed25519"
            or not isinstance(public_key_text, str)
            or not isinstance(signature_text, str)
        ):
            raise WorkerAuthenticationError(
                "WORKER_REGISTRATION_INVALID", "worker registration is invalid"
            )
        try:
            public_key = _b64decode(public_key_text)
            signature = _b64decode(signature_text)
            if len(public_key) != 32 or len(signature) != 64:
                raise ValueError
            Ed25519PublicKey.from_public_bytes(public_key).verify(
                signature, registration_proof_bytes(dict(payload))
            )
        except (ValueError, InvalidSignature, KeyError):
            raise WorkerAuthenticationError(
                "WORKER_DEVICE_SIGNATURE_INVALID", "worker device signature is invalid"
            ) from None

        now = _aware(self._clock())
        digest = hashlib.sha256(registration_token.encode("utf-8")).hexdigest()
        worker_id = f"worker_{uuid4().hex}"
        with self._engine.begin() as connection:
            token_row = connection.execute(
                update(worker_registration_tokens)
                .where(
                    worker_registration_tokens.c.token_digest == digest,
                    worker_registration_tokens.c.consumed_at.is_(None),
                    worker_registration_tokens.c.expires_at > now,
                )
                .values(consumed_at=now)
                .returning(
                    worker_registration_tokens.c.organization_id,
                    worker_registration_tokens.c.expires_at,
                )
            ).first()
            if token_row is None:
                previous = connection.execute(
                    select(worker_registration_tokens).where(
                        worker_registration_tokens.c.token_digest == digest
                    )
                ).mappings().first()
                if previous is None:
                    code = "WORKER_REGISTRATION_TOKEN_INVALID"
                elif _aware(previous["expires_at"]) <= now:
                    code = "WORKER_REGISTRATION_TOKEN_EXPIRED"
                else:
                    code = "WORKER_REGISTRATION_TOKEN_USED"
                raise WorkerAuthenticationError(code, "worker registration token is unavailable")

            organization_id = token_row.organization_id
            expires_at = datetime.fromtimestamp(
                int((now + self._session_ttl).timestamp()), UTC
            )
            jti = uuid4().hex
            credential = self._create_credential(
                worker_id=worker_id,
                organization_id=organization_id,
                jti=jti,
                expires_at=expires_at,
                issued_at=now,
            )
            connection.execute(
                insert(workers).values(
                    worker_id=worker_id,
                    organization_id=organization_id,
                    status="REGISTERED",
                    worker_version=worker_version,
                    protocol_version=protocol_version,
                    capabilities=capabilities,
                    device_key_algorithm=algorithm,
                    device_public_key=public_key,
                    created_at=now,
                )
            )
            connection.execute(
                insert(worker_session_tokens).values(
                    jti=jti,
                    organization_id=organization_id,
                    worker_id=worker_id,
                    expires_at=expires_at,
                    created_at=now,
                )
            )
        return WorkerRegistration(
            worker_id,
            organization_id,
            worker_version,
            protocol_version,
            tuple(capabilities),
            algorithm,
            public_key,
            credential,
            expires_at,
        )

    def issue_challenge(self, *, worker_id: str) -> dict[str, object]:
        now = _aware(self._clock())
        with self._engine.begin() as connection:
            worker = connection.execute(
                select(workers).where(workers.c.worker_id == worker_id)
            ).mappings().first()
            if worker is None or worker["status"] in {"DISABLED", "REVOKED"}:
                raise WorkerAuthenticationError(
                    "WORKER_NOT_REGISTERED", "worker is unavailable"
                )
            challenge_id = uuid4().hex
            nonce = secrets.token_bytes(32)
            expires_at = now + self._challenge_ttl
            connection.execute(
                insert(worker_auth_challenges).values(
                    challenge_id=challenge_id,
                    organization_id=worker["organization_id"],
                    worker_id=worker_id,
                    nonce=nonce,
                    expires_at=expires_at,
                    created_at=now,
                )
            )
        return {
            "challenge_id": challenge_id,
            "nonce": _b64encode(nonce),
            "expires_at": expires_at.isoformat(),
        }

    @staticmethod
    def session_proof_bytes(*, worker_id: str, challenge_id: str, nonce: str) -> bytes:
        return session_proof_bytes(
            worker_id=worker_id, challenge_id=challenge_id, nonce=nonce
        )

    def create_session(
        self, *, worker_id: str, challenge_id: str, device_signature: str
    ) -> WorkerSession:
        now = _aware(self._clock())
        try:
            signature = _b64decode(device_signature)
        except ValueError:
            raise WorkerAuthenticationError(
                "WORKER_DEVICE_SIGNATURE_INVALID", "worker device signature is invalid"
            ) from None
        with self._engine.begin() as connection:
            challenge = connection.execute(
                select(worker_auth_challenges)
                .where(
                    worker_auth_challenges.c.challenge_id == challenge_id,
                    worker_auth_challenges.c.worker_id == worker_id,
                )
                .with_for_update()
            ).mappings().first()
            worker = connection.execute(
                select(workers).where(workers.c.worker_id == worker_id)
            ).mappings().first()
            if challenge is None or worker is None or worker["status"] in {"DISABLED", "REVOKED"}:
                raise WorkerAuthenticationError(
                    "WORKER_CHALLENGE_INVALID", "worker challenge is unavailable"
                )
            if challenge["consumed_at"] is not None:
                raise WorkerAuthenticationError(
                    "WORKER_CHALLENGE_USED", "worker challenge was already used"
                )
            if _aware(challenge["expires_at"]) <= now:
                raise WorkerAuthenticationError(
                    "WORKER_CHALLENGE_EXPIRED", "worker challenge has expired"
                )
            message = session_proof_bytes(
                worker_id=worker_id,
                challenge_id=challenge_id,
                nonce=_b64encode(bytes(challenge["nonce"])),
            )
            try:
                Ed25519PublicKey.from_public_bytes(
                    bytes(worker["device_public_key"])
                ).verify(signature, message)
            except (ValueError, InvalidSignature):
                raise WorkerAuthenticationError(
                    "WORKER_DEVICE_SIGNATURE_INVALID", "worker device signature is invalid"
                ) from None
            consumed = connection.execute(
                update(worker_auth_challenges)
                .where(
                    worker_auth_challenges.c.challenge_id == challenge_id,
                    worker_auth_challenges.c.consumed_at.is_(None),
                    worker_auth_challenges.c.expires_at > now,
                )
                .values(consumed_at=now)
            )
            if consumed.rowcount != 1:
                raise WorkerAuthenticationError(
                    "WORKER_CHALLENGE_USED", "worker challenge was already used"
                )
            expires_at = datetime.fromtimestamp(
                int((now + self._session_ttl).timestamp()), UTC
            )
            jti = uuid4().hex
            credential = self._create_credential(
                worker_id=worker_id,
                organization_id=worker["organization_id"],
                jti=jti,
                expires_at=expires_at,
                issued_at=now,
            )
            connection.execute(
                insert(worker_session_tokens).values(
                    jti=jti,
                    organization_id=worker["organization_id"],
                    worker_id=worker_id,
                    expires_at=expires_at,
                    created_at=now,
                )
            )
        return WorkerSession(worker_id, worker["organization_id"], credential, expires_at)

    def authenticate_session(self, credential: str) -> tuple[str, str, str]:
        try:
            version, encoded, supplied_signature = credential.split(".")
            if version != "v1":
                raise ValueError
            expected = hmac.new(
                self._secret(), f"{version}.{encoded}".encode("ascii"), hashlib.sha256
            ).digest()
            if not hmac.compare_digest(expected, _b64decode(supplied_signature)):
                raise WorkerAuthenticationError(
                    "WORKER_SESSION_SIGNATURE_INVALID", "worker session signature is invalid"
                )
            payload = json.loads(_b64decode(encoded))
            worker_id = payload["worker_id"]
            organization_id = payload["organization_id"]
            jti = payload["jti"]
            expires_at = datetime.fromtimestamp(payload["exp"], UTC)
            if not all(isinstance(value, str) and value for value in (worker_id, organization_id, jti)):
                raise ValueError
        except WorkerAuthenticationError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError, OverflowError):
            raise WorkerAuthenticationError(
                "WORKER_SESSION_INVALID", "worker session credential is invalid"
            ) from None
        now = _aware(self._clock())
        if now >= expires_at:
            raise WorkerAuthenticationError(
                "WORKER_SESSION_EXPIRED", "worker session credential has expired"
            )
        with self._engine.begin() as connection:
            token = connection.execute(
                select(worker_session_tokens)
                .where(worker_session_tokens.c.jti == jti)
                .with_for_update()
            ).mappings().first()
            worker = connection.execute(
                select(workers).where(
                    workers.c.worker_id == worker_id,
                    workers.c.organization_id == organization_id,
                )
            ).mappings().first()
            if (
                token is None
                or token["worker_id"] != worker_id
                or token["organization_id"] != organization_id
                or _aware(token["expires_at"]) != expires_at
                or worker is None
                or worker["status"] in {"DISABLED", "REVOKED"}
            ):
                raise WorkerAuthenticationError(
                    "WORKER_SESSION_INVALID", "worker session credential is invalid"
                )
            if token["consumed_at"] is not None:
                raise WorkerAuthenticationError(
                    "WORKER_SESSION_REPLAYED", "worker session credential was already used"
                )
            consumed = connection.execute(
                update(worker_session_tokens)
                .where(
                    worker_session_tokens.c.jti == jti,
                    worker_session_tokens.c.consumed_at.is_(None),
                    worker_session_tokens.c.expires_at > now,
                )
                .values(consumed_at=now)
            )
            if consumed.rowcount != 1:
                raise WorkerAuthenticationError(
                    "WORKER_SESSION_REPLAYED", "worker session credential was already used"
                )
        return worker_id, organization_id, jti

    def get_worker_metadata(self, *, worker_id: str, organization_id: str) -> Mapping[str, object]:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(workers).where(
                    workers.c.worker_id == worker_id,
                    workers.c.organization_id == organization_id,
                )
            ).mappings().first()
        if row is None:
            raise WorkerAuthenticationError(
                "WORKER_NOT_REGISTERED", "worker is unavailable"
            )
        return row

    def _create_credential(
        self,
        *,
        worker_id: str,
        organization_id: str,
        jti: str,
        expires_at: datetime,
        issued_at: datetime,
    ) -> str:
        payload = {
            "exp": int(expires_at.timestamp()),
            "iat": int(issued_at.timestamp()),
            "jti": jti,
            "organization_id": organization_id,
            "worker_id": worker_id,
        }
        encoded = _b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signing_input = f"v1.{encoded}".encode("ascii")
        signature = hmac.new(self._secret(), signing_input, hashlib.sha256).digest()
        return f"v1.{encoded}.{_b64encode(signature)}"

    def _secret(self) -> bytes:
        value = self._resolve_secret()
        if not isinstance(value, bytes) or len(value) < 32:
            raise WorkerAuthenticationError(
                "WORKER_CREDENTIAL_SIGNER_UNAVAILABLE",
                "worker credential signer is unavailable",
            )
        return value
