from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from .secret_ref import InvalidSecretReferenceError, SecretRef


class SecretStoreError(RuntimeError):
    """Base error with a message safe to include in logs."""


class SecretStoreUnavailableError(SecretStoreError):
    """Raised when a secure provider is missing or unavailable."""


class SecretNotFoundError(SecretStoreError):
    """Raised when a valid reference does not identify a stored value."""


class SecretBackend(Protocol):
    def get(self, name: str) -> str | None: ...

    def put(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class SecretStore(Protocol):
    def get(self, reference: str | SecretRef) -> str: ...

    def put(self, reference: str | SecretRef, value: str) -> None: ...

    def delete(self, reference: str | SecretRef) -> None: ...


def _coerce_reference(reference: str | SecretRef, provider: str) -> SecretRef:
    parsed = SecretRef.parse(reference) if isinstance(reference, str) else reference
    if not isinstance(parsed, SecretRef) or parsed.provider != provider:
        raise InvalidSecretReferenceError(f"secret reference must target the {provider} provider")
    return parsed


class SecretResolver:
    """Resolve references to explicitly configured storage providers."""

    def __init__(self, stores: Mapping[str, SecretStore]) -> None:
        if any(provider not in {"os", "cloud"} for provider in stores):
            raise ValueError("secret store provider is not supported")
        self._stores = dict(stores)

    def _select(self, reference: str | SecretRef) -> tuple[SecretRef, SecretStore]:
        parsed = SecretRef.parse(reference) if isinstance(reference, str) else reference
        if not isinstance(parsed, SecretRef):
            raise InvalidSecretReferenceError("secret reference is invalid")
        store = self._stores.get(parsed.provider)
        if store is None:
            raise SecretStoreUnavailableError(
                f"{parsed.provider} secret store is unavailable for {parsed}"
            )
        return parsed, store

    def get(self, reference: str | SecretRef) -> str:
        parsed, store = self._select(reference)
        return store.get(parsed)

    def put(self, reference: str | SecretRef, value: str) -> None:
        parsed, store = self._select(reference)
        store.put(parsed, value)

    def delete(self, reference: str | SecretRef) -> None:
        parsed, store = self._select(reference)
        store.delete(parsed)
