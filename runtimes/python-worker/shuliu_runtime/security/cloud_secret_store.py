from __future__ import annotations

from .secret_ref import SecretRef
from .secret_store import (
    SecretBackend,
    SecretNotFoundError,
    SecretStoreError,
    SecretStoreUnavailableError,
    _coerce_reference,
)


class CloudSecretStore:
    """Provider-neutral adapter for an injected cloud secret manager."""

    def __init__(self, backend: SecretBackend | None = None) -> None:
        self._backend = backend

    def _require_backend(self, reference: SecretRef) -> SecretBackend:
        if self._backend is None:
            raise SecretStoreUnavailableError(
                f"cloud secret store is unavailable for {reference}"
            )
        return self._backend

    def get(self, reference: str | SecretRef) -> str:
        parsed = _coerce_reference(reference, "cloud")
        backend = self._require_backend(parsed)
        try:
            value = backend.get(parsed.name)
        except Exception:
            raise SecretStoreError(f"cloud secret read failed for {parsed}") from None
        if value is None:
            raise SecretNotFoundError(f"secret not found: {parsed}")
        if not isinstance(value, str):
            raise SecretStoreError(f"cloud secret read failed for {parsed}")
        return value

    def put(self, reference: str | SecretRef, value: str) -> None:
        parsed = _coerce_reference(reference, "cloud")
        if not isinstance(value, str):
            raise TypeError("secret value must be text")
        backend = self._require_backend(parsed)
        try:
            backend.put(parsed.name, value)
        except Exception:
            raise SecretStoreError(f"cloud secret write failed for {parsed}") from None

    def delete(self, reference: str | SecretRef) -> None:
        parsed = _coerce_reference(reference, "cloud")
        backend = self._require_backend(parsed)
        try:
            backend.delete(parsed.name)
        except Exception:
            raise SecretStoreError(f"cloud secret delete failed for {parsed}") from None
