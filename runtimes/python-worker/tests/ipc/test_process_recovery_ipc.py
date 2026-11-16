from __future__ import annotations

import io
import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime

from shuliu_runtime.ipc.recovery import create_process_recovery_ipc_handlers
from shuliu_runtime.ipc.server import IpcServer
from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.recovery.checkpoint import Checkpoint, CheckpointStore
from shuliu_runtime.recovery.safe_mode import SafeModeReason
from shuliu_runtime.recovery.sqlite_safe_mode import SQLiteSafeModeController
from shuliu_runtime.events.models import EventRecord
from shuliu_runtime.workflow.engine import WorkflowRun, WorkflowRunStatus
from shuliu_runtime.workflow.sqlite_repository import SQLiteWorkflowRepository


def _checkpoint(run_id: str = "run-prod-1") -> Checkpoint:
    return Checkpoint(
        run_id=run_id,
        sequence=1,
        state={"cursor": 1},
        last_completed_step=None,
        pending_action={
            "workflow_run_id": run_id,
            "organization_id": "untrusted-org",
            "tool_id": "foundation.test",
            "operation_id": "execute",
            "idempotency_key": "tool-key-1",
            "request_digest": "a" * 64,
            "step_id": "step-1",
        },
        pending_approval=None,
        context_version="workflow:v1;skill:v1",
        created_at=datetime.now(UTC),
        organization_id="org-1",
        trace_id="trace-prod-1",
    )


def _unfinished_workflow(run_id: str) -> WorkflowRun:
    now = datetime.now(UTC)
    return WorkflowRun(
        run_id=run_id,
        organization_id="org-1",
        trace_id="trace-1",
        agent_run_id=None,
        workflow_id="wf-1",
        workflow_version_id="wf-v1",
        definition_snapshot='{"workflow_id":"wf-1"}',
        input_json="{}",
        run_context_json='{"organization_id":"org-1","trace_id":"trace-1"}',
        status=WorkflowRunStatus.RUNNING,
        current_step_id="step-1",
        step_runs=(),
        resume_token=None,
        resume_token_hash=None,
        wait_reason=None,
        revision=0,
        created_at=now,
        updated_at=now,
    )


def _workflow_created_event(run: WorkflowRun) -> EventRecord:
    return EventRecord(
        event_id=f"event-{run.run_id}",
        schema_version=1,
        timestamp=run.updated_at.isoformat(),
        organization_id=run.organization_id,
        trace_id=run.trace_id,
        event_type="WORKFLOW_RUN_CREATED",
        payload={"workflow_run_id": run.run_id},
    )


def test_recovery_ipc_lists_checkpoint_metadata_and_persists_resume_decision(tmp_path):
    database = SQLiteDatabase(tmp_path / "runtime.sqlite")
    database.migrate()
    cancelled = threading.Event()
    first_handlers = create_process_recovery_ipc_handlers(database)
    assert first_handlers["runtime.recovery.begin"](
        {"transition_id": "11111111-1111-4111-8111-111111111111"}, cancelled
    ) == {
        "status": "RESUME",
        "error_code": None,
        "previous_generation": 0,
        "current_generation": 1,
        "transition_id": "11111111-1111-4111-8111-111111111111",
    }
    checkpoints = CheckpointStore(database)
    checkpoints.save(_checkpoint())
    handlers = create_process_recovery_ipc_handlers(database)
    transition_id = "22222222-2222-4222-8222-222222222222"

    assert handlers["runtime.recovery.begin"]({"transition_id": transition_id}, cancelled) == {
        "status": "RESUME",
        "error_code": None,
        "previous_generation": 1,
        "current_generation": 2,
        "transition_id": transition_id,
    }
    listed = handlers["runtime.recovery.checkpoints"]({}, cancelled)
    assert listed == {
        "checkpoints": [
            {
                "run_id": "run-prod-1",
                "sequence": 1,
                "has_pending_action": True,
                "has_pending_approval": False,
                "context_version": "workflow:v1;skill:v1",
            }
        ]
    }

    result = handlers["runtime.recovery.recover_run"](
        {"run_id": "run-prod-1", "previous_generation": 1, "current_generation": 2,
         "transition_id": transition_id},
        cancelled,
    )
    assert result == {"status": "RESUME", "error_code": None}
    assert checkpoints.latest("run-prod-1").sequence == 1
    with database.connect() as connection:
        events = [json.loads(row[0]) for row in connection.execute(
            "SELECT payload_json FROM outbox ORDER BY created_at"
        )]
    assert any(item["event_type"] == "WORKFLOW_PROCESS_CRASH_RECOVERY" for item in events)
    assert "untrusted-org" not in json.dumps(events)


def test_recovery_ipc_rejects_stale_generation_and_does_not_report_resume(tmp_path):
    database = SQLiteDatabase(tmp_path / "runtime.sqlite")
    database.migrate()
    cancelled = threading.Event()
    first_handlers = create_process_recovery_ipc_handlers(database)
    first_handlers["runtime.recovery.begin"](
        {"transition_id": "33333333-3333-4333-8333-333333333333"}, cancelled
    )
    CheckpointStore(database).save(_checkpoint())
    handlers = create_process_recovery_ipc_handlers(database)
    transition_id = "44444444-4444-4444-8444-444444444444"
    began = handlers["runtime.recovery.begin"]({"transition_id": transition_id}, cancelled)
    assert began["status"] == "RESUME"
    with database.connect() as connection:
        connection.execute(
            "UPDATE runtime_generation SET generation = 4 WHERE runtime_id = 'sidecar'"
        )
    result = handlers["runtime.recovery.recover_run"](
        {"run_id": "run-prod-1", "previous_generation": began["previous_generation"],
         "current_generation": began["current_generation"], "transition_id": transition_id},
        cancelled,
    )
    assert result == {
        "status": "SAFE_MODE",
        "error_code": "WORKER_LEASE_FENCE_UNCONFIRMED",
    }


def test_generation_transition_cannot_be_replayed_by_a_cold_main_restart(tmp_path):
    database = SQLiteDatabase(tmp_path / "runtime.sqlite")
    database.migrate()
    handlers = create_process_recovery_ipc_handlers(database)
    cancelled = threading.Event()
    first_transition = "44444444-4444-4444-8444-444444444444"
    replayed_transition = "55555555-5555-4555-8555-555555555555"

    assert handlers["runtime.recovery.begin"](
        {"transition_id": first_transition},
        cancelled,
    ) == {
        "status": "RESUME",
        "error_code": None,
        "previous_generation": 0,
        "current_generation": 1,
        "transition_id": first_transition,
    }

    second_transition = "66666666-6666-4666-8666-666666666666"
    assert handlers["runtime.recovery.begin"](
        {"transition_id": second_transition}, cancelled
    ) == {
        "status": "RESUME",
        "error_code": None,
        "previous_generation": 1,
        "current_generation": 2,
        "transition_id": second_transition,
    }
    CheckpointStore(database).save(_checkpoint())
    handlers = create_process_recovery_ipc_handlers(database)
    replayed_transition = "55555555-5555-4555-8555-555555555555"
    result = handlers["runtime.recovery.begin"](
        {"transition_id": replayed_transition},
        cancelled,
    )
    assert result == {
        "status": "RESUME",
        "error_code": None,
        "previous_generation": 2,
        "current_generation": 3,
        "transition_id": replayed_transition,
    }

    stale_run = handlers["runtime.recovery.recover_run"](
        {"run_id": "run-prod-1", "previous_generation": 1, "current_generation": 2,
         "transition_id": second_transition},
        cancelled,
    )
    assert stale_run == {
        "status": "SAFE_MODE",
        "error_code": "WORKER_LEASE_FENCE_UNCONFIRMED",
    }


def test_generation_begin_fails_closed_when_checkpoints_exist_without_generation_history(tmp_path):
    database = SQLiteDatabase(tmp_path / "runtime.sqlite")
    database.migrate()
    CheckpointStore(database).save(_checkpoint())
    handlers = create_process_recovery_ipc_handlers(database)

    result = handlers["runtime.recovery.begin"](
        {"transition_id": "66666666-6666-4666-8666-666666666666"},
        threading.Event(),
    )
    assert result == {
        "status": "SAFE_MODE",
        "error_code": "WORKER_LEASE_FENCE_UNCONFIRMED",
    }


def test_unfinished_workflow_without_checkpoint_keeps_sidecar_in_safe_mode(tmp_path):
    database = SQLiteDatabase(tmp_path / "runtime.sqlite")
    database.migrate()
    repository = SQLiteWorkflowRepository(database)
    run = _unfinished_workflow("workflow-without-checkpoint")
    assert repository.create(run, _workflow_created_event(run)) is True

    handlers = create_process_recovery_ipc_handlers(database)
    invoked: list[bool] = []
    handlers["test.normal"] = lambda _params, _cancelled: invoked.append(True) or {"ok": True}
    transition_id = "99999999-9999-4999-8999-999999999991"
    begin_request = {
        "protocol_name": "runtime-ipc",
        "protocol_version": 1,
        "message_id": "message-begin-orphan-run",
        "request_id": "request-begin-orphan-run",
        "trace_id": "trace-begin-orphan-run",
        "message_type": "request",
        "payload": {
            "method": "runtime.recovery.begin",
            "params": {"transition_id": transition_id},
        },
    }
    ordinary_request = {
        "protocol_name": "runtime-ipc",
        "protocol_version": 1,
        "message_id": "message-normal-after-orphan-run",
        "request_id": "request-normal-after-orphan-run",
        "trace_id": "trace-normal-after-orphan-run",
        "message_type": "request",
        "payload": {"method": "test.normal", "params": {}},
    }
    server = IpcServer(handlers=handlers)
    output = io.StringIO()
    server.serve(io.StringIO(json.dumps(begin_request) + "\n"), output, io.StringIO())
    begin_response = json.loads(output.getvalue())
    assert begin_response["payload"]["result"] == {
        "status": "SAFE_MODE",
        "error_code": "CHECKPOINT_NOT_FOUND",
    }
    output = io.StringIO()
    server.serve(io.StringIO(json.dumps(ordinary_request) + "\n"), output, io.StringIO())
    ordinary_response = json.loads(output.getvalue())
    assert ordinary_response["payload"]["error"]["code"] == "RUNTIME_SAFE_MODE"
    assert invoked == []
    assert SQLiteSafeModeController(database).status().run_ids == (run.run_id,)
    with database.connect() as connection:
        assert connection.execute("SELECT 1 FROM runtime_generation").fetchone() is None


def test_recovery_release_commit_wins_over_cancel_during_sqlite_transaction(tmp_path):
    database = SQLiteDatabase(tmp_path / "runtime.sqlite")
    database.migrate()
    initial_transition = "77777777-7777-4777-8777-777777777771"
    initial_handlers = create_process_recovery_ipc_handlers(database)
    cancelled = threading.Event()
    initial = initial_handlers["runtime.recovery.begin"](
        {"transition_id": initial_transition}, cancelled
    )
    assert initial["current_generation"] == 1
    assert initial_handlers["runtime.recovery.complete"](
        {"transition_id": initial_transition, "current_generation": 1}, cancelled
    ) == {"status": "RESUME", "error_code": None}

    checkpoint = _checkpoint("run-commit-wins")
    CheckpointStore(database).save(checkpoint)
    SQLiteSafeModeController(database).enter(
        run_id=checkpoint.run_id,
        reason=SafeModeReason.CONTEXT_INCOMPATIBLE,
    )
    transition_id = "77777777-7777-4777-8777-777777777772"
    handlers = create_process_recovery_ipc_handlers(database)
    begun = handlers["runtime.recovery.begin"]({"transition_id": transition_id}, cancelled)
    assert begun["current_generation"] == 2
    assert handlers["runtime.recovery.recover_run"](
        {
            "run_id": checkpoint.run_id,
            "previous_generation": 1,
            "current_generation": 2,
            "transition_id": transition_id,
        },
        cancelled,
    ) == {"status": "RESUME", "error_code": None}

    request_cancellation: dict[str, threading.Event] = {}
    complete = handlers["runtime.recovery.complete"]

    def tracked_complete(params, request_cancelled):
        request_cancellation["event"] = request_cancelled
        return complete(params, request_cancelled)

    handlers["runtime.recovery.complete"] = tracked_complete
    original_connect = database.connect

    @contextmanager
    def cancel_inside_release_transaction():
        with original_connect() as connection:
            def cancel_before_commit(statement: str) -> None:
                if statement.lstrip().upper().startswith("DELETE FROM LOCAL_TASK_STATE"):
                    request_cancellation["event"].set()

            connection.set_trace_callback(cancel_before_commit)
            yield connection

    database.connect = cancel_inside_release_transaction
    server = IpcServer(handlers=handlers, safe_mode=True)
    request = {
        "protocol_name": "runtime-ipc",
        "protocol_version": 1,
        "message_id": "message-complete",
        "request_id": "request-complete",
        "trace_id": "trace-complete",
        "message_type": "request",
        "payload": {
            "method": "runtime.recovery.complete",
            "params": {"transition_id": transition_id, "current_generation": 2},
        },
    }
    output = io.StringIO()
    server.serve(io.StringIO(json.dumps(request) + "\n"), output, io.StringIO())

    response = json.loads(output.getvalue())
    assert response["payload"]["result"] == {"status": "RESUME", "error_code": None}
    assert request_cancellation["event"].is_set()
    assert SQLiteSafeModeController(database).status().active is False
    with database.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM outbox WHERE idempotency_key = ?",
            (f"safe-mode-release:{checkpoint.run_id}:{transition_id}",),
        ).fetchone() is not None
    assert server._safe_mode is False
    assert server._health_state["status"] == "healthy"


def test_recovery_release_rejects_cancel_at_transaction_admission(tmp_path):
    database = SQLiteDatabase(tmp_path / "runtime.sqlite")
    database.migrate()
    handlers = create_process_recovery_ipc_handlers(database)
    cancelled = threading.Event()
    first_transition = "88888888-8888-4888-8888-888888888881"
    assert handlers["runtime.recovery.begin"]({"transition_id": first_transition}, cancelled)["status"] == "RESUME"
    assert handlers["runtime.recovery.complete"](
        {"transition_id": first_transition, "current_generation": 1}, cancelled
    ) == {"status": "RESUME", "error_code": None}

    checkpoint = _checkpoint("run-cancel-before-release")
    CheckpointStore(database).save(checkpoint)
    SQLiteSafeModeController(database).enter(
        run_id=checkpoint.run_id,
        reason=SafeModeReason.CONTEXT_INCOMPATIBLE,
    )
    transition_id = "88888888-8888-4888-8888-888888888882"
    assert handlers["runtime.recovery.begin"]({"transition_id": transition_id}, cancelled)["status"] == "RESUME"
    assert handlers["runtime.recovery.recover_run"](
        {
            "run_id": checkpoint.run_id,
            "previous_generation": 1,
            "current_generation": 2,
            "transition_id": transition_id,
        },
        cancelled,
    ) == {"status": "RESUME", "error_code": None}

    request_cancellation: dict[str, threading.Event] = {}
    complete = handlers["runtime.recovery.complete"]

    def tracked_complete(params, request_cancelled):
        request_cancellation["event"] = request_cancelled
        return complete(params, request_cancelled)

    handlers["runtime.recovery.complete"] = tracked_complete
    original_connect = database.connect

    @contextmanager
    def cancel_when_release_transaction_is_admitted():
        with original_connect() as connection:
            def cancel_at_transaction_admission(statement: str) -> None:
                if statement.strip().upper() == "BEGIN IMMEDIATE":
                    request_cancellation["event"].set()

            connection.set_trace_callback(cancel_at_transaction_admission)
            yield connection

    database.connect = cancel_when_release_transaction_is_admitted
    server = IpcServer(handlers=handlers, safe_mode=True)
    request = {
        "protocol_name": "runtime-ipc",
        "protocol_version": 1,
        "message_id": "message-cancel-before-release",
        "request_id": "request-cancel-before-release",
        "trace_id": "trace-cancel-before-release",
        "message_type": "request",
        "payload": {
            "method": "runtime.recovery.complete",
            "params": {"transition_id": transition_id, "current_generation": 2},
        },
    }
    output = io.StringIO()
    server.serve(io.StringIO(json.dumps(request) + "\n"), output, io.StringIO())

    response = json.loads(output.getvalue())
    assert response["payload"]["error"]["code"] == "IPC_REQUEST_CANCELLED"
    assert request_cancellation["event"].is_set()
    assert SQLiteSafeModeController(database).status().active is True
    with database.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM outbox WHERE idempotency_key = ?",
            (f"safe-mode-release:{checkpoint.run_id}:{transition_id}",),
        ).fetchone() is None
    assert server._safe_mode is True
    assert server._health_state["status"] == "safe_mode"
