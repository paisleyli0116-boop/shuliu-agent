from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from shuliu_runtime.security.secret_ref import SecretRef
from shuliu_runtime.worker.credentials import WorkerAuthenticationError, WorkerDeviceKeyStore
from shuliu_runtime.worker.registration import WorkerRegistrationClient
from tests.fakes.fake_cloud import FakeCloud


class InMemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, reference: str | SecretRef) -> str:
        key = str(reference)
        if key not in self.values:
            raise KeyError(key)
        return self.values[key]

    def put(self, reference: str | SecretRef, value: str) -> None:
        self.values[str(reference)] = value

    def delete(self, reference: str | SecretRef) -> None:
        self.values.pop(str(reference), None)


def _cloud(now: datetime) -> FakeCloud:
    return FakeCloud(
        session_signing_key=b"test-only-cloud-signing-key-32-bytes!",
        session_ttl=timedelta(minutes=5),
        clock=lambda: now,
    )


def test_registration_consumes_a_registration_token_once() -> None:
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    cloud = _cloud(now)
    token = cloud.issue_registration_token(expires_at=now + timedelta(minutes=1))
    secret_store = InMemorySecretStore()
    client = WorkerRegistrationClient(
        cloud=cloud,
        secret_store=secret_store,
        worker_version="0.1.0",
        capabilities=("worker.runtime",),
    )

    enrolled = client.register(token)

    assert enrolled.worker_id.startswith("worker_")
    record = cloud.registered_workers[enrolled.worker_id]
    assert record.worker_version == "0.1.0"
    assert record.capabilities == ("worker.runtime",)
    assert record.device_key_algorithm == "Ed25519"
    with pytest.raises(WorkerAuthenticationError) as error:
        client.register(token)
    assert error.value.code == "WORKER_REGISTRATION_TOKEN_USED"


def test_registration_rejects_an_expired_registration_token() -> None:
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    cloud = _cloud(now)
    token = cloud.issue_registration_token(expires_at=now - timedelta(seconds=1))
    secret_store = InMemorySecretStore()
    client = WorkerRegistrationClient(
        cloud=cloud, secret_store=secret_store, worker_version="0.1.0"
    )

    with pytest.raises(WorkerAuthenticationError) as error:
        client.register(token)
    assert error.value.code == "WORKER_REGISTRATION_TOKEN_EXPIRED"
    assert secret_store.values == {}


def test_worker_device_private_key_is_generated_and_kept_in_secret_store() -> None:
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    cloud = _cloud(now)
    token = cloud.issue_registration_token(expires_at=now + timedelta(minutes=1))
    secret_store = InMemorySecretStore()

    enrolled = WorkerRegistrationClient(
        cloud=cloud, secret_store=secret_store, worker_version="0.1.0"
    ).register(token)

    assert enrolled.device_key_ref.startswith("secret://os/workers/")
    assert enrolled.device_key_ref in secret_store.values
    device_keys = WorkerDeviceKeyStore(secret_store)
    assert device_keys.public_key(enrolled.device_key_ref) == cloud.registered_workers[
        enrolled.worker_id
    ].device_public_key
    assert secret_store.values[enrolled.device_key_ref] not in repr(enrolled)


def test_registration_result_redaction_never_exposes_session_credential() -> None:
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    cloud = _cloud(now)
    token = cloud.issue_registration_token(expires_at=now + timedelta(minutes=1))

    enrolled = WorkerRegistrationClient(
        cloud=cloud, secret_store=InMemorySecretStore(), worker_version="0.1.0"
    ).register(token)

    assert enrolled.session_credential
    assert enrolled.session_credential not in repr(enrolled)
    assert enrolled.session_credential not in str(enrolled.as_safe_dict())
