from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.persistence.postgres import tasks
from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.policy.rules import PolicyOutcome, PolicyRule
from shuliu_runtime.tools.broker import IdentityPrincipal, ToolBroker
from shuliu_runtime.tools.idempotency import InMemoryIdempotencyStore
from shuliu_runtime.tools.registry import ToolDefinition, ToolRegistry
from shuliu_runtime.worker.protocol import WorkerMessage
from shuliu_runtime.worker.task_runner import WorkerTaskRunner
from shuliu_runtime.worker.transport import WorkerWebSocketConnection
from sqlalchemy import select


def test_single_worker_runner_executes_tool_only_after_cloud_fencing_and_receives_result_ack(worker_harness):
    coordinator = worker_harness["coordinator"]
    principal = worker_harness["principals"]["worker-old"]
    connection = object.__new__(WorkerWebSocketConnection)
    connection._worker_id = principal.worker_id
    connection.organization_id = principal.organization_id

    def send(message_type, payload):
        if message_type == "lease_request":
            grant = coordinator.lease(
                payload["task_id"], principal,
                idempotency_key=payload["idempotency_key"],
                trace_id="trace-worker-runner",
            )
            return WorkerMessage.parse(
                worker_harness["transport"].messages[-1][1]
            )
        if message_type == "lease_authorize":
            permit = coordinator.authorize_tool_dispatch(
                principal,
                task_id=payload["task_id"],
                lease_id=payload["lease_id"],
                fencing_token=payload["fencing_token"],
                idempotency_key=payload["idempotency_key"],
                action_digest=payload["action_digest"],
            )
            return WorkerMessage(
                message_id="permit-1",
                request_id="request-1",
                trace_id="trace-worker-runner",
                message_type="lease_authorized",
                payload={
                    "task_id": permit.task_id,
                    "lease_id": permit.lease_id,
                    "fencing_token": permit.fencing_token,
                    "idempotency_key": permit.idempotency_key,
                    "action_digest": permit.action_digest,
                    "expires_at": permit.expires_at.isoformat(),
                },
            )
        if message_type == "result":
            ack = coordinator.commit_result(
                principal,
                lease_id=payload["lease_id"],
                fencing_token=payload["fencing_token"],
                idempotency_key=payload["idempotency_key"],
                result={"status": payload["status"], "result": payload["result"]},
            )
            return WorkerMessage(
                message_id="ack-1",
                request_id="request-2",
                trace_id="trace-worker-runner",
                message_type="ack",
                payload={
                    "task_id": ack.task_id,
                    "lease_id": ack.lease_id,
                    "idempotency_key": ack.idempotency_key,
                },
            )
        raise AssertionError(f"unexpected Worker message: {message_type}")

    connection.send = send
    executor_calls = []

    class Identity:
        def resolve(self, actor):
            if actor != {"subject_id": "worker-old", "organization_id": "org-1"}:
                return None
            return IdentityPrincipal("worker-old", "org-1", ("fixture.write",))

    class Executor:
        def execute(self, tool_id, args, context, idempotency_key):
            executor_calls.append((tool_id, dict(args), dict(context), idempotency_key))
            return {"ok": True, "evidence_ref": "worker-runner-evidence"}

        def verify(self, tool_id, args, output, context):
            return output.get("ok") is True

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "foundation.test",
            "1.0.0",
            {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False},
            {"type": "object", "properties": {"ok": {"type": "boolean"}, "evidence_ref": {"type": "string", "minLength": 1, "maxLength": 256, "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}(?![\s\S])"}}, "required": ["ok", "evidence_ref"], "additionalProperties": False},
            ("fixture.write",),
            RiskLevel.MEDIUM,
            True,
        ),
        Executor(),
    )
    broker = ToolBroker(
        registry=registry,
        identity=Identity(),
        policy=PolicyEngine(
            [
                PolicyRule(
                    "fixture-allow",
                    "foundation.test",
                    RiskLevel.MEDIUM,
                    PolicyOutcome.ALLOW,
                    organization_id="org-1",
                    permission="fixture.write",
                    resource_type="task",
                    privacy="INTERNAL",
                )
            ],
            policy_version="worker-runner-test-v1",
        ),
        approvals=None,
        event_sink=lambda _event: None,
        idempotency_store=InMemoryIdempotencyStore(),
    )

    ack = WorkerTaskRunner(
        connection=connection,
        tool_broker=broker,
        clock=worker_harness["clock"],
    ).run_next(
        task_id="task-1", idempotency_key="task-1-effect"
    )

    assert ack.message_type == "ack"
    assert len(executor_calls) == 1
    assert executor_calls[0][0:2] == ("foundation.test", {"value": "safe"})
    with worker_harness["engine"].connect() as db:
        task_status = db.execute(
            select(tasks.c.status).where(tasks.c.task_id == "task-1")
        ).scalar_one()
    assert task_status == "SUCCEEDED"
