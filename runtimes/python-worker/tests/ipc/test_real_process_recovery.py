from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.persistence.sqlite_idempotency import SQLiteIdempotencyStore
from shuliu_runtime.recovery.checkpoint import Checkpoint, CheckpointStore
from shuliu_runtime.recovery.safe_mode import SafeModeReason
from shuliu_runtime.recovery.sqlite_safe_mode import SQLiteSafeModeController


def _frame(request_id: str, method: str, params: dict[str, object]) -> str:
    return json.dumps(
        {
            "protocol_name": "runtime-ipc",
            "protocol_version": 1,
            "message_id": f"message-{request_id}",
            "request_id": request_id,
            "trace_id": "trace-process-recovery",
            "message_type": "request",
            "payload": {"method": method, "params": params},
        }
    ) + "\n"


def _start_sidecar(data_directory: Path):
    environment = os.environ.copy()
    environment["SHULIU_DATA_DIR"] = str(data_directory)
    environment["SHULIU_MODEL_CONNECTION_TEST"] = "0"
    process = subprocess.Popen(
        [sys.executable, "-m", "shuliu_runtime.ipc.server"],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    assert process.stdin is not None and process.stdout is not None
    output: queue.Queue[str | None] = queue.Queue()

    def read_lines() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output.put(line)
        output.put(None)

    threading.Thread(target=read_lines, daemon=True).start()
    return process, output


def _request(process: subprocess.Popen[str], output: queue.Queue[str | None], request_id: str,
             method: str, params: dict[str, object]) -> dict[str, object]:
    assert process.stdin is not None
    process.stdin.write(_frame(request_id, method, params))
    process.stdin.flush()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            line = output.get(timeout=0.1)
        except queue.Empty:
            if process.poll() is not None:
                raise AssertionError("production Sidecar exited before responding")
            continue
        if line is None:
            raise AssertionError("production Sidecar closed its IPC output")
        message = json.loads(line)
        if message.get("request_id") == request_id:
            assert message["message_type"] == "response"
            payload = message["payload"]
            return payload["result"] if "result" in payload else payload
    raise AssertionError("production Sidecar IPC response timed out")


def test_forced_sidecar_restart_recovers_dispatched_action_without_replay(tmp_path):
    database_path = tmp_path / "foundation.sqlite"
    database = SQLiteDatabase(database_path)
    database.migrate()
    first, first_output = _start_sidecar(tmp_path)
    try:
        assert _request(first, first_output, "first-health", "runtime.health", {})["status"] == "safe_mode"
        assert _request(
            first,
            first_output,
            "initial-generation",
            "runtime.recovery.begin",
            {"transition_id": "99999999-9999-4999-8999-999999999999"},
        ) == {
            "status": "RESUME",
            "error_code": None,
            "previous_generation": 0,
            "current_generation": 1,
            "transition_id": "99999999-9999-4999-8999-999999999999",
        }
        assert _request(first, first_output, "initial-checkpoints", "runtime.recovery.checkpoints", {}) == {
            "checkpoints": []
        }
        assert _request(
            first,
            first_output,
            "initial-recovery-complete",
            "runtime.recovery.complete",
            {
                "transition_id": "99999999-9999-4999-8999-999999999999",
                "current_generation": 1,
            },
        ) == {"status": "RESUME", "error_code": None}
        assert _request(first, first_output, "first-health-ready", "runtime.health", {})["status"] == "healthy"
    except Exception:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=5)
        raise

    checkpoint_store = CheckpointStore(database)
    checkpoint_store.save(
        Checkpoint(
            run_id="run-crash-1",
            sequence=1,
            state={"marker": "before-ack"},
            last_completed_step=None,
            pending_action={
                "workflow_run_id": "run-crash-1",
                "organization_id": "org-crash",
                "tool_id": "foundation.test",
                "operation_id": "execute",
                "idempotency_key": "effect-crash-1",
                "request_digest": "a" * 64,
                "step_id": "step-1",
            },
            pending_approval=None,
            context_version="workflow:v1;skill:v1",
            created_at=datetime.now(UTC),
            organization_id="org-crash",
            trace_id="trace-crash-1",
        )
    )
    store = SQLiteIdempotencyStore(database)
    scope = ("org-crash", "foundation.test", "execute")
    store.begin(scope=scope, key="effect-crash-1", request_digest="a" * 64)
    store.mark_dispatched(scope=scope, key="effect-crash-1", request_digest="a" * 64)

    try:
        first.kill()  # Windows uses TerminateProcess; POSIX uses SIGKILL.
        first.wait(timeout=5)
        assert first.returncode is not None
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=5)

    restarted, restarted_output = _start_sidecar(tmp_path)
    try:
        assert _request(restarted, restarted_output, "second-health", "runtime.health", {})["status"] == "safe_mode"
        generation = _request(
            restarted,
            restarted_output,
            "begin-recovery",
            "runtime.recovery.begin",
            {"transition_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        )
        assert generation == {
            "status": "RESUME",
            "error_code": None,
            "previous_generation": 1,
            "current_generation": 2,
            "transition_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        }
        assert _request(
            restarted, restarted_output, "list-checkpoints", "runtime.recovery.checkpoints", {}
        )["checkpoints"] == [
            {
                "run_id": "run-crash-1",
                "sequence": 1,
                "has_pending_action": True,
                "has_pending_approval": False,
                "context_version": "workflow:v1;skill:v1",
            }
        ]
        assert _request(
            restarted,
            restarted_output,
            "recover-crash",
            "runtime.recovery.recover_run",
            {"run_id": "run-crash-1", "previous_generation": 1, "current_generation": 2,
             "transition_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        ) == {"status": "WAIT", "error_code": "SIDE_EFFECT_STATE_UNKNOWN"}
        assert checkpoint_store.latest("run-crash-1").sequence == 1
        assert store.inspect(scope=scope, key="effect-crash-1", request_digest="a" * 64).status.value == "DISPATCHED"
    finally:
        if restarted.poll() is None:
            restarted.stdin.close()
            restarted.wait(timeout=5)


def test_cold_sidecar_start_enforces_persisted_safe_mode_before_serving_work(tmp_path):
    database = SQLiteDatabase(tmp_path / "foundation.sqlite")
    database.migrate()
    SQLiteSafeModeController(database).enter(
        run_id="run-safe-mode-cold-start",
        reason=SafeModeReason.CONTEXT_INCOMPATIBLE,
    )

    sidecar, output = _start_sidecar(tmp_path)
    try:
        health = _request(sidecar, output, "safe-health", "runtime.health", {})
        assert health["status"] == "safe_mode"
        assert health["safe_error_code"] == "RUNTIME_SAFE_MODE"

        # A provider connection call is denied before its handler runs. This
        # Sidecar starts with the canary gate off, so the response also proves
        # the persisted Safe Mode check wins over the ordinary canary gate.
        connection = _request(
            sidecar,
            output,
            "safe-provider-connection",
            "providers.test_connection",
            {"provider_id": "deepseek", "capability": "fast_chat"},
        )
        assert connection["error"]["code"] == "RUNTIME_SAFE_MODE"
    finally:
        if sidecar.poll() is None:
            sidecar.stdin.close()
            sidecar.wait(timeout=5)


def test_safe_mode_can_exit_only_after_current_generation_recovers_all_checkpoints(tmp_path):
    database = SQLiteDatabase(tmp_path / "foundation.sqlite")
    database.migrate()
    previous, previous_output = _start_sidecar(tmp_path)
    try:
        assert _request(
            previous,
            previous_output,
            "safe-mode-prior-generation",
            "runtime.recovery.begin",
            {"transition_id": "88888888-8888-4888-8888-888888888888"},
        )["current_generation"] == 1
    finally:
        if previous.poll() is None:
            previous.stdin.close()
            previous.wait(timeout=5)
    CheckpointStore(database).save(
        Checkpoint(
            run_id="run-safe-recover",
            sequence=1,
            state={"cursor": 1},
            last_completed_step=None,
            pending_action=None,
            pending_approval=None,
            context_version="workflow:v1;skill:v1",
            created_at=datetime.now(UTC),
            organization_id="org-safe-recover",
            trace_id="trace-safe-recover",
        )
    )
    SQLiteSafeModeController(database).enter(
        run_id="run-safe-recover",
        reason=SafeModeReason.CONTEXT_INCOMPATIBLE,
    )

    sidecar, output = _start_sidecar(tmp_path)
    transition_id = "77777777-7777-4777-8777-777777777777"
    try:
        assert _request(sidecar, output, "safe-health-before", "runtime.health", {})["status"] == "safe_mode"
        begin = _request(
            sidecar,
            output,
            "safe-recovery-begin",
            "runtime.recovery.begin",
            {"transition_id": transition_id},
        )
        assert begin["status"] == "RESUME"
        assert begin["previous_generation"] == 1
        assert begin["current_generation"] == 2

        assert _request(sidecar, output, "safe-checkpoints", "runtime.recovery.checkpoints", {}) == {
            "checkpoints": [
                {
                    "run_id": "run-safe-recover",
                    "sequence": 1,
                    "has_pending_action": False,
                    "has_pending_approval": False,
                    "context_version": "workflow:v1;skill:v1",
                }
            ]
        }
        assert _request(
            sidecar,
            output,
            "safe-recovery-premature-finish",
            "runtime.recovery.complete",
            {"transition_id": transition_id, "current_generation": 2},
        ) == {"status": "SAFE_MODE", "error_code": "SAFE_MODE_RELEASE_NOT_READY"}
        assert SQLiteSafeModeController(database).status().active is True
        recovered = _request(
            sidecar,
            output,
            "safe-recover-run",
            "runtime.recovery.recover_run",
            {
                "run_id": "run-safe-recover",
                "previous_generation": 1,
                "current_generation": 2,
                "transition_id": transition_id,
            },
        )
        assert recovered == {"status": "RESUME", "error_code": None}
        assert _request(
            sidecar,
            output,
            "safe-recovery-finish",
            "runtime.recovery.complete",
            {"transition_id": transition_id, "current_generation": 2},
        ) == {"status": "RESUME", "error_code": None}
        assert _request(sidecar, output, "safe-health-after", "runtime.health", {})["status"] == "healthy"
        # The normal Provider entry remains independently gated and no request is sent.
        denied = _request(
            sidecar,
            output,
            "safe-provider-still-disabled",
            "providers.test_connection",
            {"provider_id": "deepseek", "capability": "fast_chat"},
        )
        assert denied["status"] == "blocked"
        assert denied["safe_error_code"] == "MODEL_CONNECTION_TEST_DISABLED"
        assert denied["request_status"] == "not_sent"
        assert SQLiteSafeModeController(database).status().active is False
        with database.connect() as connection:
            events = [json.loads(row[0]) for row in connection.execute(
                "SELECT payload_json FROM outbox ORDER BY created_at"
            )]
        assert any(item.get("event_type") == "RUNTIME_SAFE_MODE_RELEASED" for item in events)
    finally:
        if sidecar.poll() is None:
            sidecar.stdin.close()
            sidecar.wait(timeout=5)


def test_sidecar_restart_advances_persistent_generation_before_recovery(tmp_path):
    first, first_output = _start_sidecar(tmp_path)
    first_transition = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    try:
        assert _request(
            first,
            first_output,
            "first-transition",
            "runtime.recovery.begin",
            {"transition_id": first_transition},
        ) == {
            "status": "RESUME",
            "error_code": None,
            "previous_generation": 0,
            "current_generation": 1,
            "transition_id": first_transition,
        }
    finally:
        if first.poll() is None:
            first.stdin.close()
            first.wait(timeout=5)

    restarted, restarted_output = _start_sidecar(tmp_path)
    try:
        next_generation = _request(
            restarted,
            restarted_output,
            "next-transition",
            "runtime.recovery.begin",
            {"transition_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"},
        )
        assert next_generation == {
            "status": "RESUME",
            "error_code": None,
            "previous_generation": 1,
            "current_generation": 2,
            "transition_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        }
    finally:
        if restarted.poll() is None:
            restarted.stdin.close()
            restarted.wait(timeout=5)
