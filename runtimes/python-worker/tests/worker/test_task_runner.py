from __future__ import annotations

import pytest

from shuliu_runtime.tools.broker import (
    PolicyOutcome,
    ToolExecutionStatus,
    ToolResult,
    VerificationStatus,
)
from shuliu_runtime.worker.credentials import WorkerAuthenticationError
from shuliu_runtime.worker.protocol import WorkerMessage
from shuliu_runtime.worker.task_runner import WorkerTaskRunner
from shuliu_runtime.worker.tool_dispatch import (
    WorkerDispatchAuthorization,
    WorkerToolDispatcher,
    worker_action_digest,
)
from datetime import UTC, datetime, timedelta


class _Connection:
    worker_id = "worker-17"
    organization_id = "org-4"

    def __init__(self, *, bad_ack=False):
        self.dispatches = []
        self.sent = []
        self.authorizations = []
        self.bad_ack = bad_ack

    def dispatch_tool(self, lease, broker, **call):
        self.dispatches.append((dict(lease), broker, call))
        clock = call.pop("clock")
        return WorkerToolDispatcher(self, broker, clock=clock).execute(lease, **call)

    def authorize_tool_dispatch(self, **lease):
        self.authorizations.append(dict(lease))
        return WorkerDispatchAuthorization(
            task_id=lease["task_id"],
            lease_id=lease["lease_id"],
            fencing_token=lease["fencing_token"],
            idempotency_key=lease["idempotency_key"],
            action_digest=lease["action_digest"],
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )

    def send(self, message_type, payload):
        self.sent.append((message_type, dict(payload)))
        return WorkerMessage(
            message_id="ack-1",
            request_id="request-1",
            trace_id="trace-1",
            message_type="ack",
            payload={
                "task_id": payload["task_id"],
                "lease_id": "wrong-lease" if self.bad_ack else payload["lease_id"],
                "idempotency_key": payload["idempotency_key"],
            },
        )


class _Broker:
    def __init__(self):
        self.calls = []

    def execute(self, tool_id, args, actor, context, *, idempotency_key):
        self.calls.append((tool_id, dict(args), dict(actor), dict(context), idempotency_key))
        return ToolResult(
            tool_id=tool_id,
            tool_version="1.0.0",
            execution_status=ToolExecutionStatus.SUCCEEDED,
            verification_status=VerificationStatus.VERIFIED,
            output={"evidence_ref": "evidence-17"},
            error_code=None,
            policy_decision=PolicyOutcome.ALLOW,
            policy_version="policy-v1",
            policy_rule_id="worker-rule",
        )


def _lease(task):
    return WorkerMessage(
        message_id="lease-message",
        request_id="request-lease",
        trace_id="trace-1",
        message_type="lease",
        payload={
            "task_id": "task-8",
            "lease_id": "lease-8",
            "fencing_token": 3,
            "idempotency_key": "task-8-effect",
            "expires_at": "2099-11-02T10:00:00+00:00",
            "task": task,
        },
    )


def test_runner_dispatches_only_the_cloud_task_tool_through_live_lease_fencing():
    connection = _Connection()
    broker = _Broker()
    runner = WorkerTaskRunner(connection=connection, tool_broker=broker)

    ack = runner.run_lease(
        _lease({"tool_call": {"tool_id": "foundation.document.write", "arguments": {"document_id": "doc-8"}}})
    )

    assert ack.message_type == "ack"
    lease, passed_broker, dispatch = connection.dispatches[0]
    assert lease["task_id"] == "task-8"
    assert passed_broker is broker
    assert dispatch["tool_id"] == "foundation.document.write"
    assert dispatch["args"] == {"document_id": "doc-8"}
    assert dispatch["actor"] == {"subject_id": "worker-17", "organization_id": "org-4"}
    assert dispatch["context"]["organization_id"] == "org-4"
    assert dispatch["context"]["trace_id"] == "trace-1"
    assert connection.authorizations[0] == {
        "task_id": "task-8",
        "lease_id": "lease-8",
        "fencing_token": 3,
        "idempotency_key": "task-8-effect",
        "action_digest": worker_action_digest(
            "foundation.document.write", {"document_id": "doc-8"}
        ),
    }
    assert broker.calls[0][3]["worker_lease"]["fencing_token"] == 3
    assert connection.sent[0][0] == "result"
    assert connection.sent[0][1]["status"] == "SUCCEEDED"


def test_runner_rejects_untyped_or_ambiguous_task_payload_before_broker_dispatch():
    connection = _Connection()
    runner = WorkerTaskRunner(connection=connection, tool_broker=_Broker())

    try:
        runner.run_lease(_lease({"tool_call": {"tool_id": "foundation.document.write", "arguments": {}, "actor": {"permissions": ["admin"]}}}))
    except WorkerAuthenticationError as error:
        assert error.code == "WORKER_TASK_PAYLOAD_INVALID"
    else:
        raise AssertionError("untrusted task fields must not be accepted")

    assert connection.dispatches == []
    assert connection.sent == []


def test_runner_fails_closed_when_legacy_authenticated_session_has_no_organization():
    connection = _Connection()
    connection.organization_id = None
    runner = WorkerTaskRunner(connection=connection, tool_broker=_Broker())

    with pytest.raises(WorkerAuthenticationError) as error:
        runner.run_lease(
            _lease(
                {
                    "tool_call": {
                        "tool_id": "foundation.document.write",
                        "arguments": {"document_id": "doc-8"},
                    }
                }
            )
        )

    assert error.value.code == "WORKER_TASK_PAYLOAD_INVALID"
    assert connection.dispatches == []
    assert connection.sent == []


def test_runner_treats_mismatched_result_ack_as_unknown_and_does_not_retry():
    connection = _Connection(bad_ack=True)
    runner = WorkerTaskRunner(connection=connection, tool_broker=_Broker())

    try:
        runner.run_lease(_lease({"tool_call": {"tool_id": "foundation.document.write", "arguments": {"document_id": "doc-8"}}}))
    except WorkerAuthenticationError as error:
        assert error.code == "WORKER_RESULT_ACK_UNKNOWN"
    else:
        raise AssertionError("mismatched ACK must be treated as unknown")

    assert len(connection.dispatches) == 1
    assert len(connection.sent) == 1
