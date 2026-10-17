from __future__ import annotations

from importlib import import_module
from types import ModuleType

from .secret_ref import SecretRef
from .secret_store import (
    SecretBackend,
    SecretNotFoundError,
    SecretStoreError,
    SecretStoreUnavailableError,
    _coerce_reference,
)


_SERVICE_NAME = "shuliu-agent"
_APPROVED_BACKENDS = frozenset(
    {
        "keyring.backends.macOS.Keyring",
        "keyring.backends.Windows.WinVaultKeyring",
        "keyring.backends.SecretService.Keyring",
        "keyring.backends.kwallet.Keyring",
        "keyring.backends.libsecret.Keyring",
    }
)


class _KeyringBackend:
    def __init__(self, backend: object, service_name: str) -> None:
        self._backend = backend
        self._service_name = service_name

    def get(self, name: str) -> str | None:
        return self._backend.get_password(self._service_name, name)

    def put(self, name: str, value: str) -> None:
        self._backend.set_password(self._service_name, name, value)

    def delete(self, name: str) -> None:
        self._backend.delete_password(self._service_name, name)


def _load_approved_backend(keyring_module: ModuleType | object | None = None) -> object:
    try:
        module = keyring_module or import_module("keyring")
        backend = module.get_keyring()
    except Exception:
        raise SecretStoreUnavailableError("OS secure storage is unavailable") from None

    backend_name = f"{type(backend).__module__}.{type(backend).__name__}"
    if backend_name not in _APPROVED_BACKENDS:
        raise SecretStoreUnavailableError("OS secure storage backend is not approved")
    return backend


class OSSecretStore:
    def __init__(
        self,
        backend: SecretBackend | None = None,
        *,
        keyring_module: ModuleType | object | None = None,
        service_name: str = _SERVICE_NAME,
    ) -> None:
        if backend is not None:
            self._backend = backend
        else:
            self._backend = _KeyringBackend(
                _load_approved_backend(keyring_module), service_name
            )

    def get(self, reference: str | SecretRef) -> str:
        parsed = _coerce_reference(reference, "os")
        try:
            value = self._backend.get(parsed.name)
        except Exception:
            raise SecretStoreError(f"secret read failed for {parsed}") from None
        if value is None:
            raise SecretNotFoundError(f"secret not found: {parsed}")
        if not isinstance(value, str):
            raise SecretStoreError(f"secret read failed for {parsed}")
        return value

    def put(self, reference: str | SecretRef, value: str) -> None:
        parsed = _coerce_reference(reference, "os")
        if not isinstance(value, str):
            raise TypeError("secret value must be text")
        try:
            self._backend.put(parsed.name, value)
        except Exception:
            raise SecretStoreError(f"secret write failed for {parsed}") from None

    def delete(self, reference: str | SecretRef) -> None:
        parsed = _coerce_reference(reference, "os")
        try:
            self._backend.delete(parsed.name)
        except Exception:
            raise SecretStoreError(f"secret delete failed for {parsed}") from None
