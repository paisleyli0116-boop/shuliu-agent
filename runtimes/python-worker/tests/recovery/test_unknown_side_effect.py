from __future__ import annotations

from datetime import UTC, datetime

import pytest

from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.recovery.checkpoint import Checkpoint, CheckpointStore
from shuliu_runtime.recovery.reconciler import (
    ActionReconciliation,
    ActionReconciliationStatus,
    RecoveryCoordinator,
    RecoveryStatus,
)

pytestmark = pytest.mark.integration


class _Verifier:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def inspect(self, run_id, pending_action):
        self.calls.append((run_id, dict(pending_action)))
        return self.result


class _CompatibleVersions:
    def is_compatible(self, checkpoint):
        return True


class _SafeMode:
    def enter(self, *, run_id, reason):
        raise AssertionError("unknown side effects must not silently enable Safe Mode")


def _database(tmp_path):
    database = SQLiteDatabase(tmp_path / "recovery.sqlite3")
    database.migrate()
    return database


def _action(**changes):
    return {
        "step_id": "external-write",
        "tool_id": "foundation.write",
        "idempotency_key": "stable-key-123",
        **changes,
    }


def _checkpoint(action):
    return Checkpoint(
        run_id="run-unknown",
        sequence=1,
        state={"cursor": 1},
        last_completed_step=None,
        pending_action=action,
        pending_approval=None,
        context_version="workflow:v1",
        created_at=datetime(2026, 10, 18, tzinfo=UTC),
        organization_id="org-1",
        trace_id="trace-1",
    )


def _coordinator(database, verifier):
    return RecoveryCoordinator(
        checkpoint_store=CheckpointStore(database),
        external_state=verifier,
        version_validator=_CompatibleVersions(),
        safe_mode=_SafeMode(),
    )


def test_unknown_external_side_effect_waits_and_never_replays_tool(tmp_path):
    database = _database(tmp_path)
    action = _action()
    CheckpointStore(database).save(_checkpoint(action))
    verifier = _Verifier(ActionReconciliation(ActionReconciliationStatus.UNKNOWN))

    result = _coordinator(database, verifier).recover("run-unknown")

    assert result.status is RecoveryStatus.WAIT
    assert result.error_code == "SIDE_EFFECT_STATE_UNKNOWN"
    assert verifier.calls == [("run-unknown", action)]
    assert result.checkpoint.pending_action == action
    with database.connect() as connection:
        event_payloads = [
            row[0]
            for row in connection.execute(
                "SELECT payload_json FROM outbox ORDER BY created_at, event_id"
            )
        ]
    assert "WORKFLOW_RECOVERY_DECISION" in "".join(event_payloads)
    assert "stable-key-123" not in "".join(event_payloads)


def test_confirmed_unapplied_action_resumes_with_original_idempotency_key(tmp_path):
    database = _database(tmp_path)
    action = _action()
    CheckpointStore(database).save(_checkpoint(action))
    verifier = _Verifier(
        ActionReconciliation(ActionReconciliationStatus.NOT_APPLIED)
    )

    result = _coordinator(database, verifier).recover("run-unknown")

    assert result.status is RecoveryStatus.RESUME
    assert verifier.calls[0][1]["idempotency_key"] == "stable-key-123"
    assert result.checkpoint.pending_action == action


def test_confirmed_applied_action_requires_verified_output_before_advancing(tmp_path):
    database = _database(tmp_path)
    action = _action()
    CheckpointStore(database).save(_checkpoint(action))
    verifier = _Verifier(ActionReconciliation(ActionReconciliationStatus.APPLIED))

    result = _coordinator(database, verifier).recover("run-unknown")

    assert result.status is RecoveryStatus.WAIT
    assert result.error_code == "APPLIED_RESULT_UNAVAILABLE"
    assert result.checkpoint.pending_action == action
    assert CheckpointStore(database).latest("run-unknown").sequence == 1


def test_confirmed_applied_action_advances_checkpoint_without_executor_call(tmp_path):
    database = _database(tmp_path)
    action = _action()
    CheckpointStore(database).save(_checkpoint(action))
    verifier = _Verifier(
        ActionReconciliation(
            ActionReconciliationStatus.APPLIED,
            verified_output={"receipt_id": "receipt-1"},
        )
    )

    result = _coordinator(database, verifier).recover("run-unknown")

    assert result.status is RecoveryStatus.RESUME
    assert result.checkpoint.pending_action is None
    assert result.checkpoint.last_completed_step == "external-write"
    assert result.checkpoint.state["reconciled_outputs"] == {
        "external-write": {"receipt_id": "receipt-1"}
    }
    assert CheckpointStore(database).latest("run-unknown") == result.checkpoint


def test_action_without_idempotency_key_is_dead_lettered(tmp_path):
    database = _database(tmp_path)
    action = _action(idempotency_key="")
    CheckpointStore(database).save(_checkpoint(action))
    verifier = _Verifier(ActionReconciliation(ActionReconciliationStatus.NOT_APPLIED))

    result = _coordinator(database, verifier).recover("run-unknown")

    assert result.status is RecoveryStatus.DEAD_LETTER
    assert result.error_code == "IDEMPOTENCY_KEY_REQUIRED"
    assert verifier.calls == []


def test_unavailable_state_verifier_waits_without_replaying_action(tmp_path):
    database = _database(tmp_path)
    action = _action()
    CheckpointStore(database).save(_checkpoint(action))

    class UnavailableVerifier:
        def inspect(self, run_id, pending_action):
            raise OSError("external state unavailable")

    result = _coordinator(database, UnavailableVerifier()).recover("run-unknown")

    assert result.status is RecoveryStatus.WAIT
    assert result.error_code == "EXTERNAL_STATE_UNAVAILABLE"
    assert result.checkpoint.pending_action == action
