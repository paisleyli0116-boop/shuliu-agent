import logging
from importlib import import_module

import pytest

from shuliu_runtime.events.observability import redact
from shuliu_runtime.security.secret_ref import SecretRef


def test_sensitive_values_are_removed_while_secret_ref_is_retained():
    result = redact(
        {
            "api_key": "credential-value",
            "secret_ref": "secret://os/provider-token",
            "nested": {"Authorization": "Bearer credential-value"},
        }
    )

    assert result == {"secret_ref": "secret://os/provider-token", "nested": {}}
    assert "credential-value" not in repr(result)


def test_backend_failure_does_not_leak_secret_value_to_error_or_log(caplog):
    module = import_module("shuliu_runtime.security")
    os_store_type = getattr(module, "OSSecretStore", None)
    secret_store_error = getattr(module, "SecretStoreError", None)
    assert os_store_type is not None and secret_store_error is not None, "secret store is not implemented"

    class FailingBackend:
        def get(self, name):
            raise RuntimeError("backend echoed backend-secret-value")

        def put(self, name, value):
            raise RuntimeError("backend echoed backend-secret-value")

        def delete(self, name):
            raise RuntimeError("backend echoed backend-secret-value")

    store = os_store_type(backend=FailingBackend())
    reference = SecretRef.parse("secret://os/provider-token")

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(secret_store_error) as error:
            store.get(reference)

    assert "backend-secret-value" not in str(error.value)
    assert "backend-secret-value" not in caplog.text
    assert "secret://os/provider-token" in str(error.value)
