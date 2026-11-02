from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from app.main import create_app
from app.persistence.postgres import metadata, tasks
from app.workers.auth import registration_proof_bytes
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine, insert, select
from sqlalchemy.pool import StaticPool


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 10, 18, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture
def worker_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    clock = MutableClock()
    app = create_app(
        engine=engine,
        session_secret_resolver=lambda: b"test-only-worker-session-key-32bytes!",
        clock=clock,
    )
    try:
        yield TestClient(app), app, engine, clock
    finally:
        engine.dispose()


def _registration_request(
    token: str,
    *,
    private_key: Ed25519PrivateKey | None = None,
    worker_version: str = "0.1.0",
    capabilities: tuple[str, ...] = ("worker.runtime",),
) -> tuple[dict[str, object], Ed25519PrivateKey]:
    key = private_key or Ed25519PrivateKey.generate()
    public_key = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    body: dict[str, object] = {
        "registration_token": token,
        "worker_version": worker_version,
        "protocol_version": 1,
        "capabilities": list(capabilities),
        "device_key_algorithm": "Ed25519",
        "device_public_key": base64.urlsafe_b64encode(public_key).rstrip(b"=").decode(),
    }
    signature = key.sign(registration_proof_bytes(body))
    body["device_signature"] = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    return body, key


def test_registration_uses_verified_device_key_and_server_assigned_identity(worker_api):
    client, app, engine, clock = worker_api
    token = app.state.worker_auth.issue_registration_token(
        organization_id="org-1", expires_at=clock() + timedelta(minutes=1)
    )
    body, _ = _registration_request(token)

    response = client.post("/v1/workers/registrations", json=body)

    assert response.status_code == 201
    registered = response.json()
    assert registered["worker_id"].startswith("worker_")
    assert registered["worker_id"] != body.get("worker_id")
    assert registered["session_credential"]
    assert "session_credential" not in repr(app.state.worker_auth)


def test_registration_rejects_invalid_device_signature_without_consuming_token(worker_api):
    client, app, engine, clock = worker_api
    token = app.state.worker_auth.issue_registration_token(
        organization_id="org-1", expires_at=clock() + timedelta(minutes=1)
    )
    body, _ = _registration_request(token)
    body["device_signature"] = base64.urlsafe_b64encode(b"x" * 64).rstrip(b"=").decode()

    first = client.post("/v1/workers/registrations", json=body)
    valid, _ = _registration_request(token)
    second = client.post("/v1/workers/registrations", json=valid)

    assert first.status_code == 401
    assert first.json()["detail"]["code"] == "WORKER_DEVICE_SIGNATURE_INVALID"
    assert second.status_code == 201


def test_registration_token_is_one_time_and_expiring(worker_api):
    client, app, engine, clock = worker_api
    token = app.state.worker_auth.issue_registration_token(
        organization_id="org-1", expires_at=clock() + timedelta(seconds=1)
    )
    body, _ = _registration_request(token)
    assert client.post("/v1/workers/registrations", json=body).status_code == 201
    assert client.post("/v1/workers/registrations", json=body).status_code == 401

    expired = app.state.worker_auth.issue_registration_token(
        organization_id="org-1", expires_at=clock() + timedelta(seconds=1)
    )
    clock.value += timedelta(seconds=2)
    expired_body, _ = _registration_request(expired)
    response = client.post("/v1/workers/registrations", json=expired_body)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "WORKER_REGISTRATION_TOKEN_EXPIRED"


def test_session_challenge_is_signed_by_registered_device_and_single_use(worker_api):
    client, app, _, clock = worker_api
    token = app.state.worker_auth.issue_registration_token(
        organization_id="org-1", expires_at=clock() + timedelta(minutes=1)
    )
    body, private_key = _registration_request(token)
    registered = client.post("/v1/workers/registrations", json=body).json()
    challenge = client.post(
        "/v1/workers/session-challenges", json={"worker_id": registered["worker_id"]}
    ).json()
    message = app.state.worker_auth.session_proof_bytes(
        worker_id=registered["worker_id"],
        challenge_id=challenge["challenge_id"],
        nonce=challenge["nonce"],
    )
    proof = base64.urlsafe_b64encode(private_key.sign(message)).rstrip(b"=").decode()
    request = {
        "worker_id": registered["worker_id"],
        "challenge_id": challenge["challenge_id"],
        "device_signature": proof,
    }

    first = client.post("/v1/workers/sessions", json=request)
    replay = client.post("/v1/workers/sessions", json=request)

    assert first.status_code == 201
    assert first.json()["session_credential"]
    assert replay.status_code == 401
    assert replay.json()["detail"]["code"] == "WORKER_CHALLENGE_USED"


def test_client_reported_worker_id_cannot_authorize_a_websocket(worker_api):
    client, app, engine, clock = worker_api
    token = app.state.worker_auth.issue_registration_token(
        organization_id="org-1", expires_at=clock() + timedelta(minutes=1)
    )
    body, _ = _registration_request(token)
    registered = client.post("/v1/workers/registrations", json=body).json()
    wrong_id = "worker_forged"

    with client.websocket_connect("/v1/workers/ws") as websocket:
        websocket.send_json({
            "protocol_name": "worker",
            "protocol_version": 1,
            "message_id": "auth-1",
            "request_id": "req-1",
            "trace_id": "trace-1",
            "message_type": "authenticate",
            "payload": {"session_credential": registered["session_credential"], "worker_id": wrong_id},
        })
        ready = websocket.receive_json()

    assert ready["payload"]["worker_id"] == registered["worker_id"]
    with engine.connect() as connection:
        assert connection.execute(select(metadata.tables["workers"]).where(
            metadata.tables["workers"].c.worker_id == wrong_id
        )).first() is None


def test_session_expiry_and_single_use_are_enforced_at_websocket_handshake(worker_api):
    client, app, _, clock = worker_api
    token = app.state.worker_auth.issue_registration_token(
        organization_id="org-1", expires_at=clock() + timedelta(minutes=1)
    )
    body, _ = _registration_request(token)
    registered = client.post("/v1/workers/registrations", json=body).json()

    with client.websocket_connect("/v1/workers/ws") as websocket:
        websocket.send_json({
            "protocol_name": "worker",
            "protocol_version": 1,
            "message_id": "auth-first",
            "request_id": "req-first",
            "trace_id": "trace-first",
            "message_type": "authenticate",
            "payload": {"session_credential": registered["session_credential"]},
        })
        assert websocket.receive_json()["message_type"] == "authenticated"

    with pytest.raises(WebSocketDisconnect) as replay:
        with client.websocket_connect("/v1/workers/ws") as websocket:
            websocket.send_json({
                "protocol_name": "worker",
                "protocol_version": 1,
                "message_id": "auth-replay",
                "request_id": "req-replay",
                "trace_id": "trace-replay",
                "message_type": "authenticate",
                "payload": {"session_credential": registered["session_credential"]},
            })
            websocket.receive_json()
    assert replay.value.code == 4401

    second_token = app.state.worker_auth.issue_registration_token(
        organization_id="org-1", expires_at=clock() + timedelta(minutes=1)
    )
    second_body, _ = _registration_request(second_token)
    second = client.post("/v1/workers/registrations", json=second_body).json()
    clock.value += timedelta(minutes=6)
    with pytest.raises(WebSocketDisconnect) as expired:
        with client.websocket_connect("/v1/workers/ws") as websocket:
            websocket.send_json({
                "protocol_name": "worker",
                "protocol_version": 1,
                "message_id": "auth-expired",
                "request_id": "req-expired",
                "trace_id": "trace-expired",
                "message_type": "authenticate",
                "payload": {"session_credential": second["session_credential"]},
            })
            websocket.receive_json()
    assert expired.value.code == 4401


def test_worker_must_receive_live_cloud_authorization_before_tool_dispatch(worker_api):
    client, app, engine, clock = worker_api
    token = app.state.worker_auth.issue_registration_token(
        organization_id="org-1", expires_at=clock() + timedelta(minutes=1)
    )
    body, _ = _registration_request(token)
    registered = client.post("/v1/workers/registrations", json=body).json()
    with engine.begin() as connection:
        connection.execute(
            insert(tasks).values(
                task_id="task-fenced",
                organization_id="org-1",
                status="READY",
                payload={"required_capabilities": ["worker.runtime"]},
                created_at=clock(),
            )
        )

    with client.websocket_connect("/v1/workers/ws") as websocket:
        websocket.send_json({
            "protocol_name": "worker",
            "protocol_version": 1,
            "message_id": "auth-fenced",
            "request_id": "req-auth-fenced",
            "trace_id": "trace-fenced",
            "message_type": "authenticate",
            "payload": {"session_credential": registered["session_credential"]},
        })
        assert websocket.receive_json()["message_type"] == "authenticated"
        websocket.send_json({
            "protocol_name": "worker",
            "protocol_version": 1,
            "message_id": "lease-fenced",
            "request_id": "req-lease-fenced",
            "trace_id": "trace-fenced",
            "message_type": "lease_request",
            "payload": {"task_id": "task-fenced", "idempotency_key": "effect-fenced"},
        })
        lease_message = websocket.receive_json()
        assert lease_message["message_type"] == "lease"
        lease = lease_message["payload"]

        websocket.send_json({
            "protocol_name": "worker",
            "protocol_version": 1,
            "message_id": "authorize-fenced",
            "request_id": "req-authorize-fenced",
            "trace_id": "trace-fenced",
            "message_type": "lease_authorize",
            "payload": {
                "task_id": lease["task_id"],
                "lease_id": lease["lease_id"],
                "fencing_token": lease["fencing_token"],
                "idempotency_key": lease["idempotency_key"],
            },
        })
        authorized = websocket.receive_json()
        assert authorized["message_type"] == "lease_authorized"
        assert authorized["payload"]["fencing_token"] == lease["fencing_token"]

        clock.value += timedelta(seconds=31)
        websocket.send_json({
            "protocol_name": "worker",
            "protocol_version": 1,
            "message_id": "authorize-expired",
            "request_id": "req-authorize-expired",
            "trace_id": "trace-expired",
            "message_type": "lease_authorize",
            "payload": {
                "task_id": lease["task_id"],
                "lease_id": lease["lease_id"],
                "fencing_token": lease["fencing_token"],
                "idempotency_key": lease["idempotency_key"],
            },
        })
        rejected = websocket.receive_json()

    assert rejected["message_type"] == "error"
    assert rejected["payload"]["code"] == "WORKER_LEASE_EXPIRED"
