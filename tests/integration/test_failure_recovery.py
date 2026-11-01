from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = REPO_ROOT / "runtimes" / "python-worker"
pytestmark = pytest.mark.integration


def _env(database_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    paths = [str(RUNTIME_ROOT)]
    if environment.get("PYTHONPATH"):
        paths.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    environment["TASK34_DB"] = str(database_path)
    return environment


def test_schema_incompatible_checkpoint_after_process_exit_enters_safe_mode(tmp_path: Path):
    database_path = tmp_path / "incompatible-checkpoint.sqlite3"
    seed_process = r'''
import os
from datetime import UTC, datetime
from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.recovery.checkpoint import Checkpoint, CheckpointStore

database = SQLiteDatabase(os.environ["TASK34_DB"])
database.migrate()
CheckpointStore(database).save(Checkpoint(
    run_id="run-bad-version", sequence=1, state={"cursor": 1},
    last_completed_step=None, pending_action=None, pending_approval=None,
    context_version="workflow:v1", created_at=datetime.now(UTC),
    organization_id="org-1", trace_id="trace-bad-version",
))
with database.connect() as connection:
    connection.execute(
        "UPDATE checkpoints SET schema_version = 99 WHERE task_id = ?",
        ("run-bad-version",),
    )
os._exit(23)
'''
    seeded = subprocess.run(
        [sys.executable, "-c", seed_process],
        cwd=REPO_ROOT,
        env=_env(database_path),
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert seeded.returncode == 23

    recovery_process = r'''
import json
import os
from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.recovery.checkpoint import CheckpointStore
from shuliu_runtime.recovery.process_recovery import ProcessCrashRecovery
from shuliu_runtime.recovery.reconciler import RecoveryCoordinator

database = SQLiteDatabase(os.environ["TASK34_DB"])
store = CheckpointStore(database)

class ExternalState:
    def inspect(self, run_id, pending_action):
        raise AssertionError("incompatible checkpoint must not inspect side effects")

class Versions:
    def is_compatible(self, checkpoint):
        return True

class SafeMode:
    def __init__(self): self.reasons = []
    def enter(self, *, run_id, reason): self.reasons.append(reason.value)

class Fence:
    def fence_previous(self, run_id, *, previous_generation, current_generation):
        return current_generation > previous_generation

safe_mode = SafeMode()
reconciler = RecoveryCoordinator(
    checkpoint_store=store, external_state=ExternalState(),
    version_validator=Versions(), safe_mode=safe_mode,
)
recovery = ProcessCrashRecovery(
    recovery=reconciler, checkpoint_store=store, fence=Fence(), events=store,
)
decision = recovery.recover_process(
    "run-bad-version", previous_generation=1, current_generation=2
)
print(json.dumps({
    "status": decision.status.value,
    "error_code": decision.error_code,
    "safe_mode_reasons": safe_mode.reasons,
}))
'''
    recovered = subprocess.run(
        [sys.executable, "-c", recovery_process],
        cwd=REPO_ROOT,
        env=_env(database_path),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert recovered.returncode == 0
    assert json.loads(recovered.stdout) == {
        "status": "SAFE_MODE",
        "error_code": "CHECKPOINT_SCHEMA_INCOMPATIBLE",
        "safe_mode_reasons": ["CHECKPOINT_INCOMPATIBLE"],
    }
    assert "schema mismatch details" not in recovered.stdout
    assert "schema mismatch details" not in recovered.stderr
