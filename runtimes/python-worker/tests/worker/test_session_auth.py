from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from shuliu_runtime.worker.credentials import WorkerAuthenticationError
from shuliu_runtime.worker.protocol import WorkerMessage, WorkerProtocolError
from tests.fakes.fake_cloud import FakeCloud


def _enrolled_cloud(now: datetime) -> tuple[FakeCloud, str]:
    cloud = FakeCloud(
        session_signing_key=b"test-only-cloud-signing-key-32-bytes!",
        session_ttl=timedelta(seconds=30),
        clock=lambda: now,
    )
    token = cloud.issue_registration_token(expires_at=now + timedelta(minutes=1))
    result = cloud.register(
        token,
        b"x" * 32,
        device_key_algorithm="Ed25519",
        worker_version="0.1.0",
        capabilities=(),
    )
    return cloud, result.session_credential


def test_authenticate_accepts_a_valid_short_lived_session_credential() -> None:
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    cloud, credential = _enrolled_cloud(now)

    session = cloud.authenticate(credential)

    assert session.worker_id.startswith("worker_")
    assert session.expires_at == now + timedelta(seconds=30)


def test_authenticate_rejects_a_tampered_session_signature() -> None:
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    cloud, credential = _enrolled_cloud(now)
    token_parts = credential.split(".")
    token_parts[-1] = ("A" if token_parts[-1][-1] != "A" else "B") + token_parts[-1][1:]

    with pytest.raises(WorkerAuthenticationError) as error:
        cloud.authenticate(".".join(token_parts))
    assert error.value.code == "WORKER_SESSION_SIGNATURE_INVALID"


def test_authenticate_rejects_an_expired_session_credential() -> None:
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    cloud, credential = _enrolled_cloud(now)
    cloud.set_time(now + timedelta(seconds=31))

    with pytest.raises(WorkerAuthenticationError) as error:
        cloud.authenticate(credential)
    assert error.value.code == "WORKER_SESSION_EXPIRED"


def test_worker_protocol_rejects_an_unknown_version() -> None:
    message = {
        "protocol_name": "worker",
        "protocol_version": 2,
        "message_id": "message-1",
        "request_id": "request-1",
        "trace_id": "trace-1",
        "message_type": "heartbeat",
        "payload": {"worker_id": "worker_01", "sent_at": "2026-10-18T00:00:00Z"},
    }

    with pytest.raises(WorkerProtocolError) as error:
        WorkerMessage.parse(message)
    assert error.value.code == "PROTOCOL_VERSION_UNSUPPORTED"
