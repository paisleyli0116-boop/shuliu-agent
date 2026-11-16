from __future__ import annotations

import ssl

import pytest

from shuliu_runtime.worker.credentials import WorkerAuthenticationError
from shuliu_runtime.worker.protocol import WorkerMessage, WorkerProtocolError, validate_worker_message
from shuliu_runtime.worker.transport import WorkerWebSocketConnection


def _message(message_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "protocol_name": "worker",
        "protocol_version": 1,
        "message_id": "message-1",
        "request_id": "request-1",
        "trace_id": "trace-1",
        "message_type": message_type,
        "payload": payload,
    }


def test_worker_lease_authorization_protocol_requires_exact_lease_generation_fields():
    request = {
        "task_id": "task-1",
        "lease_id": "lease-1",
        "fencing_token": 2,
        "idempotency_key": "effect-1",
        "action_digest": "a" * 64,
    }
    response = {**request, "expires_at": "2026-11-02T10:00:00+00:00"}
    validate_worker_message(_message("lease_authorize", request))
    validate_worker_message(_message("lease_authorized", response))
    with pytest.raises(WorkerProtocolError):
        validate_worker_message(_message("lease_authorize", {**request, "fencing_token": True}))


def test_worker_v1_authentication_keeps_organization_id_optional_for_compatibility(monkeypatch):
    authenticated = _message("authenticated", {"worker_id": "worker_01"})
    validate_worker_message(authenticated)

    class SocketContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr("shuliu_runtime.worker.transport.connect", lambda *_args, **_kwargs: SocketContext())
    connection = WorkerWebSocketConnection(
        websocket_url="wss://worker.example.test/v1/workers/ws",
        worker_id="worker_01",
        session_credential="test-session-credential",
        ssl_context=ssl.create_default_context(),
        timeout_seconds=1,
    )
    connection.send = lambda *_args, **_kwargs: WorkerMessage.parse(authenticated)

    connection.__enter__()

    assert connection.worker_id == "worker_01"
    assert connection.organization_id is None
    connection.close()


def test_worker_v1_legacy_lease_authorization_frames_remain_parseable():
    legacy_request = _message(
        "lease_authorize",
        {
            "task_id": "task-1",
            "lease_id": "lease-1",
            "fencing_token": 2,
            "idempotency_key": "effect-1",
        },
    )
    legacy_response = _message(
        "lease_authorized",
        {
            "task_id": "task-1",
            "lease_id": "lease-1",
            "fencing_token": 2,
            "idempotency_key": "effect-1",
            "expires_at": "2026-11-02T10:00:00+00:00",
        },
    )

    validate_worker_message(legacy_request)
    validate_worker_message(legacy_response)


def test_worker_websocket_only_accepts_matching_cloud_dispatch_authorization():
    connection = object.__new__(WorkerWebSocketConnection)
    connection.send = lambda message_type, payload: WorkerMessage(
        message_id="message-2",
        request_id="request-1",
        trace_id="trace-1",
        message_type="lease_authorized",
        payload={
            "task_id": "task-1",
            "lease_id": "lease-1",
            "fencing_token": 2,
            "idempotency_key": "effect-1",
            "action_digest": "a" * 64,
            "expires_at": "2099-11-02T10:00:00+00:00",
        },
    )
    authorization = connection.authorize_tool_dispatch(
        task_id="task-1",
        lease_id="lease-1",
        fencing_token=2,
        idempotency_key="effect-1",
        action_digest="a" * 64,
    )
    assert authorization.task_id == "task-1"
    assert authorization.lease_id == "lease-1"
    assert authorization.fencing_token == 2
    assert authorization.idempotency_key == "effect-1"
    assert authorization.action_digest == "a" * 64
    assert authorization.expires_at.year == 2099

    connection.send = lambda _message_type, _payload: WorkerMessage(
        message_id="message-3",
        request_id="request-2",
        trace_id="trace-2",
        message_type="lease_authorized",
        payload={
            "task_id": "task-1",
            "lease_id": "lease-other",
            "fencing_token": 2,
            "idempotency_key": "effect-1",
            "action_digest": "a" * 64,
            "expires_at": "2099-11-02T10:00:00+00:00",
        },
    )
    with pytest.raises(WorkerAuthenticationError) as error:
        connection.authorize_tool_dispatch(
            task_id="task-1",
            lease_id="lease-1",
            fencing_token=2,
            idempotency_key="effect-1",
            action_digest="a" * 64,
        )
    assert error.value.code == "WORKER_LEASE_AUTHORIZATION_INVALID"


def test_worker_websocket_rejects_legacy_unbound_permit_before_tool_dispatch():
    connection = object.__new__(WorkerWebSocketConnection)
    connection.send = lambda _message_type, _payload: WorkerMessage.parse(
        _message(
            "lease_authorized",
            {
                "task_id": "task-1",
                "lease_id": "lease-1",
                "fencing_token": 2,
                "idempotency_key": "effect-1",
                "expires_at": "2099-11-02T10:00:00+00:00",
            },
        )
    )

    with pytest.raises(WorkerAuthenticationError) as error:
        connection.authorize_tool_dispatch(
            task_id="task-1",
            lease_id="lease-1",
            fencing_token=2,
            idempotency_key="effect-1",
            action_digest="a" * 64,
        )

    assert error.value.code == "WORKER_LEASE_AUTHORIZATION_INVALID"


def test_worker_websocket_dispatches_to_broker_only_after_matching_cloud_permit():
    connection = object.__new__(WorkerWebSocketConnection)
    connection.send = lambda _message_type, payload: WorkerMessage(
        message_id="message-4",
        request_id="request-4",
        trace_id="trace-4",
        message_type="lease_authorized",
        payload={
            **payload,
            "expires_at": "2099-11-02T10:00:00+00:00",
        },
    )

    class Broker:
        def __init__(self):
            self.calls = []

        def execute(self, tool_id, args, actor, context, *, idempotency_key):
            self.calls.append((tool_id, args, actor, context, idempotency_key))
            return {"verified": True}

    broker = Broker()
    result = connection.dispatch_tool(
        {
            "task_id": "task-1",
            "lease_id": "lease-1",
            "fencing_token": 2,
            "idempotency_key": "effect-1",
        },
        broker,
        tool_id="foundation.test",
        args={"value": "safe"},
        actor={"subject_id": "worker-1"},
        context={"organization_id": "org-1"},
    )

    assert result == {"verified": True}
    assert len(broker.calls) == 1
    assert broker.calls[0][3]["worker_lease"]["fencing_token"] == 2
