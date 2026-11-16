from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from shuliu_runtime.ipc.bootstrap import bootstrap_runtime
from shuliu_runtime.ipc.recovery import create_process_recovery_ipc_handlers
from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.persistence.sqlite_idempotency import SQLiteIdempotencyStore
from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.policy.rules import PolicyDecision, PolicyOutcome
from shuliu_runtime.tools.broker import (
    IdentityPrincipal,
    ToolBroker,
    ToolExecutionError,
)
from shuliu_runtime.tools.registry import ToolDefinition, ToolRegistry
from shuliu_runtime.workflow.engine import WorkflowRunStatus
from shuliu_runtime.workflow.sqlite_repository import SQLiteWorkflowRepository
from shuliu_runtime.worker.credentials import WorkerAuthenticationError
from shuliu_runtime.worker.protocol import WorkerMessage
from shuliu_runtime.worker.task_runner import WorkerTaskRunner
from shuliu_runtime.worker.tool_dispatch import (
    WorkerDispatchAuthorization,
    WorkerToolDispatcher,
)


_CHILD = r'''
import sys
import time
from pathlib import Path
from shuliu_runtime.ipc.bootstrap import bootstrap_runtime
from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.policy.rules import PolicyDecision, PolicyOutcome
from shuliu_runtime.tools.broker import IdentityPrincipal, ToolBroker
from shuliu_runtime.tools.registry import ToolDefinition, ToolRegistry
from shuliu_runtime.workflow.definition import WorkflowDefinition
from shuliu_runtime.workflow.engine import WorkflowEngine
from shuliu_runtime.persistence.sqlite_idempotency import SQLiteIdempotencyStore

database_dir = Path(sys.argv[1])
signal = Path(sys.argv[2])
bootstrap = bootstrap_runtime(database_dir)

class Identity:
    def resolve(self, actor):
        if actor.get("subject_id") != "test-agent":
            return None
        return IdentityPrincipal("test-agent", "org-test", ("fixture.write",))

class Policy:
    def evaluate(self, **request):
        return PolicyDecision(PolicyOutcome.ALLOW, RiskLevel.MEDIUM, "fixture-allow", "fixture-v1", "test", request["context"]["trace_id"])

class Effect:
    def execute(self, tool_id, args, context, idempotency_key):
        with signal.open("a", encoding="utf-8") as handle:
            handle.write("external-effect\n")
            handle.flush()
        # The parent kills this process after the durable dispatch marker and
        # fixture side effect exist, but before ToolBroker can record an ACK.
        while True:
            time.sleep(60)
    def verify(self, tool_id, args, output, context):
        return True

registry = ToolRegistry()
registry.register(
    ToolDefinition(
        "fixture.write", "1.0.0",
        {"type":"object","properties":{"value":{"type":"string"}},"required":["value"],"additionalProperties":False},
        {"type":"object","properties":{"ok":{"type":"boolean"},"evidence_ref":{"type":"string","minLength":1,"maxLength":256,"pattern":r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}(?![\s\S])"}},"required":["ok","evidence_ref"],"additionalProperties":False},
        ("fixture.write",), RiskLevel.MEDIUM, True,
    ),
    Effect(),
)
broker = ToolBroker(
    registry=registry, identity=Identity(), policy=Policy(), approvals=None,
    event_sink=lambda event: None,
    idempotency_store=SQLiteIdempotencyStore(bootstrap.database),
)
definition = WorkflowDefinition.from_dict({
    "workflow_id":"crash-fixture", "workflow_version_id":"v1",
    "input_schema":{"type":"object","required":["value"],"properties":{"value":{"type":"string"}},"additionalProperties":False},
    "steps":[{"id":"write","type":"tool","tool_id":"fixture.write","arguments":{"value":{"$ref":"input.value"}},"next":"END"}],
})
WorkflowEngine(repository=bootstrap.workflow_repository, tool_broker=broker).start_workflow(
    definition, {"value":"safe"},
    {"organization_id":"org-test","trace_id":"trace-crash-fixture","privacy":"INTERNAL","actor":{"subject_id":"test-agent"}},
)
'''


_WORKER_CHILD = r'''
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from shuliu_runtime.ipc.bootstrap import bootstrap_runtime
from shuliu_runtime.persistence.sqlite_idempotency import SQLiteIdempotencyStore
from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.policy.rules import PolicyDecision, PolicyOutcome
from shuliu_runtime.tools.broker import IdentityPrincipal, ToolBroker, ToolExecutionError, ToolExecutionStatus, ToolResult, VerificationStatus
from shuliu_runtime.tools.registry import ToolDefinition, ToolRegistry
from shuliu_runtime.worker.credentials import WorkerAuthenticationError
from shuliu_runtime.worker.protocol import WorkerMessage
from shuliu_runtime.worker.task_runner import WorkerTaskRunner
from shuliu_runtime.worker.tool_dispatch import WorkerDispatchAuthorization, WorkerToolDispatcher

data_dir, marker, outcome_path, mode = map(str, sys.argv[1:5])
bootstrap = bootstrap_runtime(data_dir)

class Identity:
    def resolve(self, actor):
        if actor != {"subject_id": "worker-crash", "organization_id": "org-crash"}:
            return None
        return IdentityPrincipal("worker-crash", "org-crash", ("fixture.write",))

class Policy:
    def evaluate(self, **request):
        return PolicyDecision(PolicyOutcome.ALLOW, RiskLevel.MEDIUM, "fixture-allow", "fixture-v1", "test", request["context"]["trace_id"])

class Executor:
    def execute(self, tool_id, args, context, idempotency_key):
        with Path(marker).open("a", encoding="utf-8") as handle:
            handle.write("external-effect\n")
            handle.flush()
        if mode == "dispatch":
            while True:
                time.sleep(60)
        return {"ok": True, "evidence_ref": "worker-crash-effect"}
    def verify(self, tool_id, args, output, context):
        return True

registry = ToolRegistry()
registry.register(
    ToolDefinition(
        "fixture.write", "1.0.0",
        {"type":"object","properties":{"value":{"type":"string"}},"required":["value"],"additionalProperties":False},
        {"type":"object","properties":{"ok":{"type":"boolean"},"evidence_ref":{"type":"string","minLength":1,"maxLength":256,"pattern":r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}(?![\s\S])"}},"required":["ok","evidence_ref"],"additionalProperties":False},
        ("fixture.write",), RiskLevel.MEDIUM, True,
    ), Executor(),
)
broker = ToolBroker(
    registry=registry, identity=Identity(), policy=Policy(), approvals=None,
    event_sink=lambda event: None,
    idempotency_store=SQLiteIdempotencyStore(bootstrap.database),
)

class Connection:
    worker_id = "worker-crash"
    organization_id = "org-crash"
    def authorize_tool_dispatch(self, *, task_id, lease_id, fencing_token, idempotency_key, action_digest):
        return WorkerDispatchAuthorization(task_id, lease_id, fencing_token, idempotency_key, action_digest, datetime.now(UTC) + timedelta(minutes=5))
    def dispatch_tool(self, lease, tool_broker, **call):
        clock = call.pop("clock", None)
        dispatcher = WorkerToolDispatcher(self, tool_broker) if clock is None else WorkerToolDispatcher(self, tool_broker, clock=clock)
        return dispatcher.execute(lease, **call)
    def send(self, message_type, payload):
        Path(outcome_path).write_text(message_type, encoding="utf-8")
        return WorkerMessage("ack-message", "ack-request", "trace-worker-crash", "ack", {
            "task_id": payload["task_id"], "lease_id": payload["lease_id"], "idempotency_key": payload["idempotency_key"]
        })

lease = WorkerMessage("lease-message", "lease-request", "trace-worker-crash", "lease", {
    "task_id":"task-worker-crash", "lease_id":"lease-worker-crash", "fencing_token":1,
    "idempotency_key":"worker-crash-effect", "expires_at":"2099-11-02T10:00:00+00:00",
    "task":{"tool_call":{"tool_id":"fixture.write","arguments":{"value":"safe"}}},
})
try:
    WorkerTaskRunner(connection=Connection(), tool_broker=broker).run_lease(lease)
except ToolExecutionError as error:
    if mode == "replay" and error.code == "RECONCILIATION_REQUIRED":
        Path(outcome_path).write_text(error.code, encoding="utf-8")
    else:
        raise
'''


def _registry_and_broker(database: SQLiteDatabase, effects: list[str]) -> ToolBroker:
    class Identity:
        def resolve(self, actor):
            if actor.get("subject_id") != "test-agent":
                return None
            return IdentityPrincipal("test-agent", "org-test", ("fixture.write",))

    class Policy:
        def evaluate(self, **request):
            return PolicyDecision(
                PolicyOutcome.ALLOW,
                RiskLevel.MEDIUM,
                "fixture-allow",
                "fixture-v1",
                "test",
                request["context"]["trace_id"],
            )

    class Effect:
        def execute(self, *_args, **_kwargs):
            effects.append("unexpected-replay")
            return {"ok": True, "evidence_ref": "fixture-replay"}

        def verify(self, *_args, **_kwargs):
            return True

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "fixture.write",
            "1.0.0",
            {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False},
            {"type": "object", "properties": {"ok": {"type": "boolean"}, "evidence_ref": {"type": "string", "minLength": 1, "maxLength": 256, "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}(?![\s\S])"}}, "required": ["ok", "evidence_ref"], "additionalProperties": False},
            ("fixture.write",),
            RiskLevel.MEDIUM,
            True,
        ),
        Effect(),
    )
    return ToolBroker(
        registry=registry,
        identity=Identity(),
        policy=Policy(),
        approvals=None,
        event_sink=lambda _event: None,
        idempotency_store=SQLiteIdempotencyStore(database),
    )


def test_real_workflow_dispatch_crash_before_ack_enters_safe_mode_and_never_replays(tmp_path):
    runtime_root = Path(__file__).resolve().parents[2]
    data_dir = tmp_path / "runtime-data"
    marker = tmp_path / "external-effects.log"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(runtime_root) + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(data_dir), str(marker)],
        cwd=runtime_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not marker.exists():
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=2)
                pytest.fail(f"child exited before dispatch marker: {process.returncode}; stdout={stdout!r}; stderr={stderr!r}")
            time.sleep(0.02)
        assert marker.exists(), "real Tool executor did not reach the dispatch/ACK window"
        process.kill()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    database = SQLiteDatabase(data_dir / "foundation.sqlite")
    repository = SQLiteWorkflowRepository(database)
    runs = repository.list_unfinished()
    assert len(runs) == 1
    run = runs[0]
    assert run.status is WorkflowRunStatus.RUNNING
    assert run.current_step_id == "write"
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status FROM tool_idempotency WHERE tool_id = 'fixture.write'"
        ).fetchone()
    assert row is not None and row["status"] == "DISPATCHED"

    handlers = create_process_recovery_ipc_handlers(database)
    import threading

    outcome = handlers["runtime.recovery.begin"](
        {"transition_id": "33333333-3333-4333-8333-333333333333"}, threading.Event()
    )
    assert outcome == {
        "status": "SAFE_MODE",
        "error_code": "CHECKPOINT_NOT_FOUND",
    }

    effects: list[str] = []
    replay_broker = _registry_and_broker(database, effects)
    context = json.loads(run.run_context_json)
    context["workflow_run_id"] = run.run_id
    context["workflow_step_id"] = "write"
    key = "workflow:" + hashlib.sha256(
        f"{run.organization_id}:{run.run_id}:write".encode()
    ).hexdigest()
    with pytest.raises(ToolExecutionError) as error:
        replay_broker.execute(
            "fixture.write",
            {"value": "safe"},
            {"subject_id": "test-agent"},
            context,
            idempotency_key=key,
        )
    assert error.value.code == "RECONCILIATION_REQUIRED"
    assert effects == []
    assert marker.read_text(encoding="utf-8").splitlines() == ["external-effect"]


def test_worker_runner_crash_after_tool_dispatch_before_cloud_ack_never_replays(tmp_path):
    runtime_root = Path(__file__).resolve().parents[2]
    data_dir = tmp_path / "worker-runtime-data"
    marker = tmp_path / "worker-external-effects.log"
    outcome = tmp_path / "worker-replay-outcome.txt"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(runtime_root) + os.pathsep + env.get("PYTHONPATH", "")

    process = subprocess.Popen(
        [sys.executable, "-c", _WORKER_CHILD, str(data_dir), str(marker), str(outcome), "dispatch"],
        cwd=runtime_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not marker.exists():
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=2)
                pytest.fail(f"Worker child exited before Tool dispatch: {process.returncode}; stdout={stdout!r}; stderr={stderr!r}")
            time.sleep(0.02)
        assert marker.exists(), "Worker runner did not reach the Tool dispatch/ACK window"
        process.kill()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    database = SQLiteDatabase(data_dir / "foundation.sqlite")
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status FROM tool_idempotency WHERE tool_id = 'fixture.write'"
        ).fetchone()
    assert row is not None and row["status"] == "DISPATCHED"

    replay = subprocess.run(
        [sys.executable, "-c", _WORKER_CHILD, str(data_dir), str(marker), str(outcome), "replay"],
        cwd=runtime_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert replay.returncode == 0, f"Worker replay exited {replay.returncode}: {replay.stderr}"
    assert outcome.read_text(encoding="utf-8") == "RECONCILIATION_REQUIRED"
    assert marker.read_text(encoding="utf-8").splitlines() == ["external-effect"]
