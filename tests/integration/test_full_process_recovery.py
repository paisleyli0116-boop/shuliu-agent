from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = REPO_ROOT / "runtimes" / "python-worker"
pytestmark = pytest.mark.integration


def _environment(**values: str) -> dict[str, str]:
    environment = os.environ.copy()
    paths = [str(RUNTIME_ROOT)]
    existing = environment.get("PYTHONPATH")
    if existing:
        paths.append(existing)
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    environment.update(values)
    return environment


def _start_side_effect_process(database_path: Path, ready_path: Path) -> subprocess.Popen[bytes]:
    child = r'''
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.persistence.sqlite_idempotency import SQLiteIdempotencyStore
from shuliu_runtime.recovery.checkpoint import Checkpoint, CheckpointStore

database = SQLiteDatabase(os.environ["TASK34_DB"])
database.migrate()
store = SQLiteIdempotencyStore(database)
scope = ("org-recovery", "foundation.record.write", "workflow-step-1")
claim = store.begin(scope=scope, key="recovery-key-1", request_digest="d" * 64)
if claim.status.value != "NEW":
    raise SystemExit(41)
store.mark_dispatched(scope=scope, key="recovery-key-1", request_digest="d" * 64)
with database.connect() as connection:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS task34_effect_probe "
        "(run_id TEXT PRIMARY KEY, calls INTEGER NOT NULL, receipt_id TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO task34_effect_probe (run_id, calls, receipt_id) VALUES (?, 1, ?)",
        ("run-process-1", "receipt-process-1"),
    )
CheckpointStore(database).save(
    Checkpoint(
        run_id="run-process-1",
        sequence=1,
        state={"cursor": "write"},
        last_completed_step=None,
        pending_action={
            "step_id": "write",
            "tool_id": "foundation.record.write",
            "idempotency_key": "recovery-key-1",
            "arguments": {"private": "do-not-audit-this-value"},
        },
        pending_approval=None,
        context_version="workflow:v1",
        created_at=datetime.now(UTC),
        organization_id="org-recovery",
        trace_id="trace-process-1",
    )
)
Path(os.environ["TASK34_READY_FILE"]).write_text("ready", encoding="utf-8")
sys.stdin.readline()
'''
    return subprocess.Popen(
        [sys.executable, "-c", child],
        cwd=REPO_ROOT,
        env=_environment(TASK34_DB=str(database_path), TASK34_READY_FILE=str(ready_path)),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _recover_in_new_process(
    database_path: Path,
    prior_exit_code: int,
    effect_state: str,
    *,
    exit_observed: bool = True,
) -> dict[str, object]:
    child = r'''
import json
import os
from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.persistence.sqlite_idempotency import SQLiteIdempotencyStore
from shuliu_runtime.recovery.checkpoint import CheckpointStore
from shuliu_runtime.recovery.process_recovery import ProcessCrashRecovery
from shuliu_runtime.recovery.reconciler import (
    ActionReconciliation, ActionReconciliationStatus, RecoveryCoordinator,
)

database = SQLiteDatabase(os.environ["TASK34_DB"])
checkpoint_store = CheckpointStore(database)
idempotency = SQLiteIdempotencyStore(database)
claim = idempotency.begin(
    scope=("org-recovery", "foundation.record.write", "workflow-step-1"),
    key="recovery-key-1",
    request_digest="d" * 64,
)
if claim.status.value != "RECONCILIATION_REQUIRED":
    raise SystemExit(42)

class ExternalState:
    def inspect(self, run_id, pending_action):
        with database.connect() as connection:
            row = connection.execute(
                "SELECT calls, receipt_id FROM task34_effect_probe WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if os.environ["TASK34_EFFECT_STATE"] == "UNKNOWN":
            return ActionReconciliation(ActionReconciliationStatus.UNKNOWN)
        if row is None:
            return ActionReconciliation(ActionReconciliationStatus.NOT_APPLIED)
        return ActionReconciliation(
            ActionReconciliationStatus.APPLIED,
            {"receipt_id": row["receipt_id"]},
        )

class Versions:
    def is_compatible(self, checkpoint):
        return True

class SafeMode:
    def enter(self, *, run_id, reason):
        pass

class ObservedExitFence:
    def fence_previous(self, run_id, *, previous_generation, current_generation):
        return (
            os.environ.get("TASK34_PREVIOUS_EXIT_OBSERVED") == "1"
            and int(os.environ["TASK34_PREVIOUS_EXIT_CODE"]) != 0
            and current_generation > previous_generation
        )

reconciler = RecoveryCoordinator(
    checkpoint_store=checkpoint_store,
    external_state=ExternalState(),
    version_validator=Versions(),
    safe_mode=SafeMode(),
)
recovery = ProcessCrashRecovery(
    recovery=reconciler,
    checkpoint_store=checkpoint_store,
    fence=ObservedExitFence(),
    events=checkpoint_store,
)
decision = recovery.recover_process(
    "run-process-1", previous_generation=1, current_generation=2
)
checkpoint = checkpoint_store.latest("run-process-1")
print(json.dumps({
    "status": decision.status.value,
    "error_code": decision.error_code,
    "sequence": checkpoint.sequence if checkpoint else None,
    "pending_action": checkpoint.pending_action is not None if checkpoint else None,
}))
'''
    completed = subprocess.run(
        [sys.executable, "-c", child],
        cwd=REPO_ROOT,
        env=_environment(
            TASK34_DB=str(database_path),
            TASK34_EFFECT_STATE=effect_state,
            TASK34_PREVIOUS_EXIT_OBSERVED="1" if exit_observed else "0",
            TASK34_PREVIOUS_EXIT_CODE=str(prior_exit_code),
        ),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, "recovery subprocess did not complete successfully"
    return json.loads(completed.stdout)


@pytest.mark.parametrize(
    ("effect_state", "exit_observed", "expected_status", "expected_error", "expected_sequence", "pending_action"),
    [
        ("APPLIED", True, "RESUME", None, 2, False),
        ("UNKNOWN", True, "WAIT", "SIDE_EFFECT_STATE_UNKNOWN", 1, True),
        ("UNKNOWN", False, "SAFE_MODE", "WORKER_LEASE_FENCE_UNCONFIRMED", 1, True),
    ],
)
def test_real_process_exit_and_reconstruction_reconcile_without_replaying_effect(
    tmp_path: Path,
    effect_state: str,
    exit_observed: bool,
    expected_status: str,
    expected_error: str | None,
    expected_sequence: int,
    pending_action: bool,
):
    database_path = tmp_path / "process-recovery.sqlite3"
    ready_path = tmp_path / "runtime-ready"
    process = _start_side_effect_process(database_path, ready_path)
    try:
        deadline = time.monotonic() + 10
        while not ready_path.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready_path.exists(), f"runtime did not reach crash injection point (exit={process.poll()})"
        process.kill()
        exit_code = process.wait(timeout=10)
        assert exit_code != 0

        result = _recover_in_new_process(
            database_path, exit_code, effect_state, exit_observed=exit_observed
        )
        assert result == {
            "status": expected_status,
            "error_code": expected_error,
            "sequence": expected_sequence,
            "pending_action": pending_action,
        }

        database = __import__("shuliu_runtime.persistence.sqlite", fromlist=["SQLiteDatabase"]).SQLiteDatabase(database_path)
        with database.connect() as connection:
            calls = connection.execute(
                "SELECT calls FROM task34_effect_probe WHERE run_id = ?",
                ("run-process-1",),
            ).fetchone()[0]
            crash_events = [
                row[0]
                for row in connection.execute(
                    "SELECT payload_json FROM outbox WHERE idempotency_key LIKE 'process-crash-recovery:%'"
                )
            ]
        assert calls == 1
        assert len(crash_events) == 1
        assert "do-not-audit-this-value" not in crash_events[0]
        assert "recovery-key-1" not in crash_events[0]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_real_process_exit_while_approval_pending_remains_waiting(tmp_path: Path):
    database_path = tmp_path / "approval-recovery.sqlite3"
    ready_path = tmp_path / "approval-runtime-ready"
    child = r'''
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.recovery.checkpoint import Checkpoint, CheckpointStore

database = SQLiteDatabase(os.environ["TASK34_DB"])
database.migrate()
CheckpointStore(database).save(Checkpoint(
    run_id="run-approval-1",
    sequence=1,
    state={"cursor": "approval"},
    last_completed_step=None,
    pending_action=None,
    pending_approval={"approval_id": "approval-1", "step_id": "write"},
    context_version="workflow:v1",
    created_at=datetime.now(UTC),
    organization_id="org-recovery",
    trace_id="trace-approval-1",
))
Path(os.environ["TASK34_READY_FILE"]).write_text("ready", encoding="utf-8")
sys.stdin.readline()
'''
    process = subprocess.Popen(
        [sys.executable, "-c", child],
        cwd=REPO_ROOT,
        env=_environment(TASK34_DB=str(database_path), TASK34_READY_FILE=str(ready_path)),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready_path.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready_path.exists(), f"approval runtime did not reach crash point (exit={process.poll()})"
        process.kill()
        exit_code = process.wait(timeout=10)
        assert exit_code != 0

        recovery_script = r'''
import json
import os
from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.recovery.checkpoint import CheckpointStore
from shuliu_runtime.recovery.process_recovery import ProcessCrashRecovery
from shuliu_runtime.recovery.reconciler import RecoveryCoordinator

database = SQLiteDatabase(os.environ["TASK34_DB"])
store = CheckpointStore(database)
external_calls = []

class ExternalState:
    def inspect(self, run_id, pending_action):
        external_calls.append(run_id)
        raise AssertionError("pending approval must be checked before external state")

class Versions:
    def is_compatible(self, checkpoint):
        return True

class SafeMode:
    def enter(self, *, run_id, reason):
        pass

class ObservedExitFence:
    def fence_previous(self, run_id, *, previous_generation, current_generation):
        return (
            int(os.environ["TASK34_PREVIOUS_EXIT_CODE"]) != 0
            and current_generation > previous_generation
        )

reconciler = RecoveryCoordinator(
    checkpoint_store=store,
    external_state=ExternalState(),
    version_validator=Versions(),
    safe_mode=SafeMode(),
)
recovery = ProcessCrashRecovery(
    recovery=reconciler, checkpoint_store=store,
    fence=ObservedExitFence(), events=store,
)
decision = recovery.recover_process(
    "run-approval-1", previous_generation=1, current_generation=2,
)
checkpoint = store.latest("run-approval-1")
print(json.dumps({
    "status": decision.status.value,
    "error_code": decision.error_code,
    "sequence": checkpoint.sequence if checkpoint else None,
    "approval_pending": checkpoint.pending_approval is not None if checkpoint else None,
    "external_calls": len(external_calls),
}))
'''
        completed = subprocess.run(
            [sys.executable, "-c", recovery_script],
            cwd=REPO_ROOT,
            env=_environment(
                TASK34_DB=str(database_path),
                TASK34_PREVIOUS_EXIT_CODE=str(exit_code),
            ),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert completed.returncode == 0, "approval recovery subprocess failed"
        assert json.loads(completed.stdout) == {
            "status": "WAIT",
            "error_code": "APPROVAL_PENDING",
            "sequence": 1,
            "approval_pending": True,
            "external_calls": 0,
        }
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
