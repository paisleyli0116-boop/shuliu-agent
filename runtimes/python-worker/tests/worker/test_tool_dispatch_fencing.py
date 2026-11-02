from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from shuliu_runtime.worker.credentials import WorkerAuthenticationError
from shuliu_runtime.worker.tool_dispatch import WorkerDispatchAuthorization, WorkerToolDispatcher


class _Authorizer:
    def __init__(self, error: Exception | None = None, authorization=None) -> None:
        self.error = error
        self.authorization = authorization
        self.calls = []

    def authorize_tool_dispatch(self, **lease):
        self.calls.append(dict(lease))
        if self.error is not None:
            raise self.error
        return self.authorization


class _Broker:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, tool_id, args, actor, context, *, idempotency_key):
        self.calls.append((tool_id, dict(args), dict(actor), dict(context), idempotency_key))
        return {"status": "executed"}


def _lease():
    return {
        "task_id": "task-1",
        "lease_id": "lease-1",
        "fencing_token": 2,
        "idempotency_key": "task-1-effect",
    }


def test_worker_tool_dispatch_does_not_call_broker_when_lease_is_expired_or_fenced():
    authorizer = _Authorizer(RuntimeError("WORKER_LEASE_EXPIRED"))
    broker = _Broker()
    dispatcher = WorkerToolDispatcher(authorizer, broker)

    with pytest.raises(RuntimeError, match="WORKER_LEASE_EXPIRED"):
        dispatcher.execute(
            _lease(),
            tool_id="foundation.test",
            args={"value": "safe"},
            actor={"subject_id": "worker-1"},
            context={"organization_id": "org-1"},
        )

    assert len(authorizer.calls) == 1
    assert broker.calls == []


def test_worker_tool_dispatch_passes_verified_lease_identity_to_the_tool_broker():
    authorization = WorkerDispatchAuthorization(
        task_id="task-1",
        lease_id="lease-1",
        fencing_token=2,
        idempotency_key="task-1-effect",
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    authorizer = _Authorizer(authorization=authorization)
    broker = _Broker()
    dispatcher = WorkerToolDispatcher(authorizer, broker)
    lease = _lease()

    result = dispatcher.execute(
        lease,
        tool_id="foundation.test",
        args={"value": "safe"},
        actor={"subject_id": "worker-1"},
        context={"organization_id": "org-1"},
    )

    assert result == {"status": "executed"}
    assert authorizer.calls == [lease]
    assert len(broker.calls) == 1
    worker_lease = broker.calls[0][3]["worker_lease"]
    assert {key: worker_lease[key] for key in lease} == lease
    assert worker_lease["authorization_expires_at"] == authorization.expires_at.isoformat()
    assert broker.calls[0][4] == "task-1-effect"


def test_worker_tool_dispatch_rejects_authorization_that_expires_before_broker_dispatch():
    authorization = WorkerDispatchAuthorization(
        task_id="task-1",
        lease_id="lease-1",
        fencing_token=2,
        idempotency_key="task-1-effect",
        expires_at=datetime(2026, 11, 2, 9, 59, tzinfo=UTC),
    )
    authorizer = _Authorizer(authorization=authorization)
    broker = _Broker()
    dispatcher = WorkerToolDispatcher(
        authorizer,
        broker,
        clock=lambda: datetime(2026, 11, 2, 10, 0, tzinfo=UTC),
    )

    with pytest.raises(WorkerAuthenticationError) as error:
        dispatcher.execute(
            _lease(),
            tool_id="foundation.test",
            args={"value": "safe"},
            actor={"subject_id": "worker-1"},
            context={"organization_id": "org-1"},
        )

    assert error.value.code == "WORKER_LEASE_EXPIRED"
    assert len(authorizer.calls) == 1
    assert broker.calls == []


def test_worker_tool_dispatch_rejects_malformed_lease_before_authorizing_or_running_tool():
    authorizer = _Authorizer()
    broker = _Broker()
    dispatcher = WorkerToolDispatcher(authorizer, broker)
    lease = _lease()
    lease["fencing_token"] = True

    with pytest.raises(ValueError, match="lease"):
        dispatcher.execute(
            lease,
            tool_id="foundation.test",
            args={},
            actor={"subject_id": "worker-1"},
            context={"organization_id": "org-1"},
        )

    assert authorizer.calls == []
    assert broker.calls == []
