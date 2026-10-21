from importlib import import_module
import sys
from types import SimpleNamespace

import pytest

from shuliu_runtime.security.secret_ref import InvalidSecretReferenceError, SecretRef


def _store_api():
    module = import_module("shuliu_runtime.security")
    names = (
        "CloudSecretStore",
        "OSSecretStore",
        "SecretNotFoundError",
        "SecretResolver",
        "SecretStoreError",
        "SecretStoreUnavailableError",
    )
    missing = [name for name in names if getattr(module, name, None) is None]
    assert not missing, f"secret store contract is missing: {', '.join(missing)}"
    return module


class MemoryBackend:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def put(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def test_secret_ref_parses_a_provider_scoped_reference():
    module = import_module("shuliu_runtime.security.secret_ref")
    secret_ref_type = getattr(module, "SecretRef", None)

    assert secret_ref_type is not None, "SecretRef parser is not implemented"
    reference = secret_ref_type.parse("secret://os/provider-token")

    assert reference.provider == "os"
    assert reference.name == "provider-token"
    assert str(reference) == "secret://os/provider-token"


def test_os_secret_store_round_trips_through_its_secure_backend():
    module = _store_api()
    backend = MemoryBackend()
    store = module.OSSecretStore(backend=backend)
    reference = SecretRef.parse("secret://os/provider-token")

    store.put(reference, "test-secret-value")

    assert store.get(reference) == "test-secret-value"
    assert backend.values == {"provider-token": "test-secret-value"}
    store.delete(reference)
    with pytest.raises(module.SecretNotFoundError):
        store.get(reference)


def test_cloud_secret_store_delegates_to_a_replaceable_backend():
    module = _store_api()
    backend = MemoryBackend()
    store = module.CloudSecretStore(backend=backend)
    reference = SecretRef.parse("secret://cloud/worker/refresh")

    store.put(reference, "cloud-secret-value")

    assert store.get(reference) == "cloud-secret-value"
    store.delete(reference)
    with pytest.raises(module.SecretNotFoundError):
        store.get(reference)


def test_cloud_secret_store_without_a_configured_backend_fails_closed():
    module = _store_api()
    store = module.CloudSecretStore()

    with pytest.raises(module.SecretStoreUnavailableError):
        store.get("secret://cloud/worker/refresh")


def test_resolver_routes_by_reference_and_rejects_unknown_provider():
    module = _store_api()
    resolver = module.SecretResolver(
        {
            "os": module.OSSecretStore(backend=MemoryBackend()),
            "cloud": module.CloudSecretStore(backend=MemoryBackend()),
        }
    )

    resolver.put("secret://cloud/worker/session", "short-lived-value")
    assert resolver.get("secret://cloud/worker/session") == "short-lived-value"

    unavailable = module.SecretResolver({})
    with pytest.raises(module.SecretStoreUnavailableError):
        unavailable.get("secret://cloud/unknown")


def test_os_secret_store_fails_closed_for_an_unapproved_keyring_backend():
    module = _store_api()

    class PlaintextBackend:
        def get_password(self, service, username):
            return None

    keyring_module = SimpleNamespace(get_keyring=lambda: PlaintextBackend())
    with pytest.raises(module.SecretStoreUnavailableError):
        module.OSSecretStore(keyring_module=keyring_module)


def test_windows_selects_the_approved_vault_backend_without_entry_point_discovery(monkeypatch):
    secure_store = import_module("shuliu_runtime.security.os_secret_store")
    vault_backend = type(
        "WinVaultKeyring",
        (),
        {"__module__": "keyring.backends.Windows"},
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        secure_store,
        "import_module",
        lambda name: SimpleNamespace(WinVaultKeyring=vault_backend)
        if name == "keyring.backends.Windows"
        else pytest.fail("Windows must not use plugin entry-point discovery"),
    )

    backend = secure_store._load_approved_backend()

    assert isinstance(backend, vault_backend)


def test_unknown_secret_reference_is_not_resolved_implicitly():
    module = _store_api()
    resolver = module.SecretResolver({"os": module.OSSecretStore(backend=MemoryBackend())})

    with pytest.raises(InvalidSecretReferenceError):
        resolver.get("secret://third-party/raw")
