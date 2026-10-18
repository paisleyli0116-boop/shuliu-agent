from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from shuliu_runtime.security.secret_ref import SecretRef
from shuliu_runtime.security.secret_store import SecretStore


class WorkerAuthenticationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WorkerRegistrationOutcomeUnknown(RuntimeError):
    """A registration response was lost after the request may have reached Cloud."""

    def __init__(self, device_key_ref: str) -> None:
        super().__init__("worker registration outcome is unknown; reconcile before retrying")
        self.code = "WORKER_REGISTRATION_OUTCOME_UNKNOWN"
        self.device_key_ref = device_key_ref


@dataclass(frozen=True)
class RegistrationGrant:
    worker_id: str
    session_credential: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class WorkerRegistrationResult:
    worker_id: str
    session_credential: str = field(repr=False)
    expires_at: datetime
    device_key_ref: str

    def as_safe_dict(self) -> dict[str, str]:
        return {
            "worker_id": self.worker_id,
            "expires_at": self.expires_at.isoformat(),
            "device_key_ref": self.device_key_ref,
        }


class WorkerDeviceKeyStore:
    """Creates device keys and stores private material only through SecretStore."""

    algorithm = "Ed25519"

    def __init__(self, secret_store: SecretStore) -> None:
        self._secret_store = secret_store

    def create(self) -> tuple[SecretRef, bytes]:
        private_key = Ed25519PrivateKey.generate()
        private_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        reference = SecretRef("os", f"workers/{uuid4().hex}/device-key")
        encoded_private_key = base64.urlsafe_b64encode(private_bytes).decode("ascii")
        self._secret_store.put(reference, encoded_private_key)
        public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return reference, public_key

    def load(self, reference: str | SecretRef) -> Ed25519PrivateKey:
        try:
            encoded = self._secret_store.get(reference)
            private_bytes = base64.urlsafe_b64decode(encoded.encode("ascii"))
            return Ed25519PrivateKey.from_private_bytes(private_bytes)
        except Exception:
            raise WorkerAuthenticationError(
                "WORKER_DEVICE_KEY_UNAVAILABLE", "worker device key is unavailable"
            ) from None

    def public_key(self, reference: str | SecretRef) -> bytes:
        return self.load(reference).public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def delete(self, reference: str | SecretRef) -> None:
        self._secret_store.delete(reference)
