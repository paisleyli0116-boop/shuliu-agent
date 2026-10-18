from __future__ import annotations

from typing import Protocol

from shuliu_runtime.security.secret_store import SecretStore
from shuliu_runtime.worker.credentials import (
    RegistrationGrant,
    WorkerAuthenticationError,
    WorkerDeviceKeyStore,
    WorkerRegistrationOutcomeUnknown,
    WorkerRegistrationResult,
)


class WorkerRegistrationCloud(Protocol):
    def register(
        self,
        registration_token: str,
        device_public_key: bytes,
        *,
        device_key_algorithm: str,
        worker_version: str,
        capabilities: tuple[str, ...],
    ) -> RegistrationGrant: ...


class WorkerRegistrationClient:
    def __init__(
        self,
        *,
        cloud: WorkerRegistrationCloud,
        secret_store: SecretStore,
        worker_version: str,
        capabilities: tuple[str, ...] = (),
    ) -> None:
        if not isinstance(worker_version, str) or not worker_version.strip():
            raise ValueError("worker version is required")
        if any(not isinstance(item, str) or not item.strip() for item in capabilities):
            raise ValueError("worker capabilities must be non-empty strings")
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("worker capabilities must be unique")
        self._cloud = cloud
        self._device_keys = WorkerDeviceKeyStore(secret_store)
        self._worker_version = worker_version
        self._capabilities = capabilities

    def register(self, registration_token: str) -> WorkerRegistrationResult:
        if not isinstance(registration_token, str) or not registration_token:
            raise WorkerAuthenticationError(
                "WORKER_REGISTRATION_TOKEN_INVALID", "worker registration token is invalid"
            )
        device_key_ref, public_key = self._device_keys.create()
        try:
            grant = self._cloud.register(
                registration_token,
                public_key,
                device_key_algorithm=self._device_keys.algorithm,
                worker_version=self._worker_version,
                capabilities=self._capabilities,
            )
        except WorkerAuthenticationError:
            self._device_keys.delete(device_key_ref)
            raise
        except Exception:
            # The request may have consumed a one-time token; retain the key for reconciliation.
            raise WorkerRegistrationOutcomeUnknown(str(device_key_ref)) from None
        return WorkerRegistrationResult(
            worker_id=grant.worker_id,
            session_credential=grant.session_credential,
            expires_at=grant.expires_at,
            device_key_ref=str(device_key_ref),
        )
