from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import pytest

from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.recovery.checkpoint import (
    Checkpoint,
    CheckpointStore,
    IncompatibleCheckpointVersion,
)
from shuliu_runtime.recovery.process_recovery import ProcessCrashRecovery
from shuliu_runtime.recovery.reconciler import (
    ActionReconciliation,
    ActionReconciliationStatus,
    RecoveryCoordinator,
    RecoveryStatus,
)
from shuliu_runtime.recovery.safe_mode import SafeModeReason

pytestmark = pytest.mark.integration


def _database(tmp_path):
    database = SQLiteDatabase(tmp_path / "foundation.sqlite3")
    database.migrate()
    return database


def _checkpoint(
    *, sequence=1, pending_action=None, pending_approval=None, state=None
):
    return Checkpoint(
        run_id="run-1",
        sequence=sequence,
        state={"cursor": sequence} if state is None else state,
        last_completed_step=None,
        pending_action=pending_action,
        pending_approval=pending_approval,
        context_version="workflow:v1;skill:v1",
        created_at=datetime(2026, 10, 18, tzinfo=UTC),
        organization_id="org-1",
        trace_id="trace-1",
    )


class _Verifier:
    def __init__(self, result=None):
        self.result = result or ActionReconciliation(ActionReconciliationStatus.UNKNOWN)
        self.calls = []

    def inspect(self, run_id, pending_action):
        self.calls.append((run_id, dict(pending_action)))
        return self.result


class _VersionValidator:
    def __init__(self, compatible=True):
        self.compatible = compatible

    def is_compatible(self, checkpoint):
        return self.compatible


class _SafeMode:
    def __init__(self):
        self.transitions = []

    def enter(self, *, run_id, reason):
        self.transitions.append((run_id, reason))


class _ConfirmedFence:
    def fence_previous(self, run_id, *, previous_generation, current_generation):
        return True


def _coordinator(database, verifier=None, compatible=True, safe_mode=None):
    return RecoveryCoordinator(
        checkpoint_store=CheckpointStore(database),
        external_state=verifier or _Verifier(),
        version_validator=_VersionValidator(compatible),
        safe_mode=safe_mode or _SafeMode(),
    )


def test_checkpoint_and_event_persist_across_database_reopen(tmp_path):
    database_path = tmp_path / "foundation.sqlite3"
    first_database = SQLiteDatabase(database_path)
    first_database.migrate()
    store = CheckpointStore(first_database)

    store.save(_checkpoint(sequence=1))
    store.save(_checkpoint(sequence=2, state={"cursor": 2}))

    reopened = SQLiteDatabase(database_path)
    latest = CheckpointStore(reopened).latest("run-1")
    assert latest.sequence == 2
    assert latest.state == {"cursor": 2}

    with reopened.connect() as connection:
        outbox_events = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT payload_json FROM outbox ORDER BY created_at, event_id"
            )
        ]
    assert [event["event_type"] for event in outbox_events] == [
        "CHECKPOINT_SAVED",
        "CHECKPOINT_SAVED",
    ]
    assert outbox_events[-1]["payload"] == {
        "run_id": "run-1",
        "sequence": 2,
        "context_version": "workflow:v1;skill:v1",
    }


def test_same_checkpoint_sequence_cannot_be_reused_with_different_state(tmp_path):
    store = CheckpointStore(_database(tmp_path))
    store.save(_checkpoint())

    with pytest.raises(ValueError, match="sequence"):
        store.save(_checkpoint(state={"cursor": "different"}))


def test_identical_checkpoint_save_is_idempotent_and_stale_sequence_is_rejected(
    tmp_path,
):
    database = _database(tmp_path)
    store = CheckpointStore(database)
    latest = _checkpoint(sequence=2, state={"cursor": 2})

    assert store.save(latest) == latest
    assert store.save(latest) == latest
    with pytest.raises(ValueError, match="backwards"):
        store.save(_checkpoint(sequence=1))

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1


def test_checkpoint_event_contains_metadata_but_not_state_or_pending_action(tmp_path):
    database = _database(tmp_path)
    action = {
        "step_id": "send",
        "tool_id": "foundation.send",
        "idempotency_key": "private-key",
        "arguments": {"message": "private workflow content"},
    }
    CheckpointStore(database).save(
        _checkpoint(
            pending_action=action,
            state={"private": "workflow state"},
        )
    )

    with database.connect() as connection:
        payload = json.loads(
            connection.execute("SELECT payload_json FROM outbox").fetchone()[0]
        )

    assert payload["payload"]["run_id"] == "run-1"
    assert "private" not in json.dumps(payload)
    assert "private-key" not in json.dumps(payload)
    assert "arguments" not in json.dumps(payload)


def test_process_crash_event_is_idempotent_and_contains_only_safe_recovery_metadata(tmp_path):
    database = _database(tmp_path)
    checkpoint = _checkpoint(
        pending_action={
            "step_id": "send",
            "tool_id": "foundation.send",
            "idempotency_key": "private-key",
            "arguments": {"message": "private workflow content"},
        }
    )
    store = CheckpointStore(database)

    store.record_process_crash(
        checkpoint,
        previous_generation=2,
        current_generation=3,
        status="WAIT",
        error_code="SIDE_EFFECT_STATE_UNKNOWN",
    )
    store.record_process_crash(
        checkpoint,
        previous_generation=2,
        current_generation=3,
        status="WAIT",
        error_code="SIDE_EFFECT_STATE_UNKNOWN",
    )

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT payload_json FROM outbox WHERE idempotency_key LIKE 'process-crash-recovery:%'"
        ).fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload_json"])
    assert payload["event_type"] == "WORKFLOW_PROCESS_CRASH_RECOVERY"
    assert payload["trace_id"] == "trace-1"
    assert payload["payload"] == {
        "run_id": "run-1",
        "previous_generation": 2,
        "current_generation": 3,
        "status": "WAIT",
        "error_code": "SIDE_EFFECT_STATE_UNKNOWN",
    }
    assert "private-key" not in rows[0]["payload_json"]
    assert "private workflow content" not in rows[0]["payload_json"]


def test_process_recovery_can_resume_after_approval_for_same_generation_pair(tmp_path):
    database = _database(tmp_path)
    store = CheckpointStore(database)
    waiting = _checkpoint(
        pending_action={
            "step_id": "send",
            "tool_id": "foundation.send",
            "idempotency_key": "idem-1",
        },
        pending_approval={"approval_id": "approval-1"},
    )
    store.save(waiting)
    recovery = RecoveryCoordinator(
        checkpoint_store=store,
        external_state=_Verifier(),
        version_validator=_VersionValidator(),
        safe_mode=_SafeMode(),
    )
    process_recovery = ProcessCrashRecovery(
        recovery=recovery,
        checkpoint_store=store,
        fence=_ConfirmedFence(),
        events=store,
    )

    first = process_recovery.recover_process(
        "run-1", previous_generation=2, current_generation=3
    )
    assert first.status is RecoveryStatus.WAIT
    assert first.error_code == "APPROVAL_PENDING"

    # Approval completion is persisted as a newer checkpoint while the Sidecar
    # remains in the same generation pair and retries the guarded recovery.
    resumed = Checkpoint(
        run_id="run-1",
        sequence=2,
        state={"cursor": 2},
        last_completed_step="approval",
        pending_action=None,
        pending_approval=None,
        context_version="workflow:v1;skill:v1",
        created_at=datetime(2026, 10, 18, 0, 0, 1, tzinfo=UTC),
        organization_id="org-1",
        trace_id="trace-1",
    )
    store.save(resumed)

    second = process_recovery.recover_process(
        "run-1", previous_generation=2, current_generation=3
    )
    assert second.status is RecoveryStatus.RESUME
    assert second.error_code is None

    # A replay of the same outcome must remain idempotent.
    third = process_recovery.recover_process(
        "run-1", previous_generation=2, current_generation=3
    )
    assert third.status is RecoveryStatus.RESUME

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT idempotency_key, payload_json FROM outbox "
            "WHERE idempotency_key LIKE 'process-crash-recovery:%' ORDER BY idempotency_key"
        ).fetchall()
    assert len(rows) == 2
    events = [json.loads(row["payload_json"]) for row in rows]
    assert {event["payload"]["status"] for event in events} == {"WAIT", "RESUME"}
    assert {event["payload"]["error_code"] for event in events} == {
        "APPROVAL_PENDING",
        None,
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": "PROCESSING"},
        {"run_id": "run/../../private"},
        {"organization_id": "org/../../private"},
        {"trace_id": "trace\nsecret"},
        {"error_code": "SYNTHETIC_SECRET_MARKER"},
    ],
)
def test_process_crash_event_rejects_untrusted_audit_identity(tmp_path, kwargs):
    from dataclasses import replace

    database = _database(tmp_path)
    checkpoint = replace(_checkpoint(), **{
        key: value
        for key, value in kwargs.items()
        if key in {"run_id", "organization_id", "trace_id"}
    })
    store = CheckpointStore(database)

    with pytest.raises(ValueError):
        store.record_process_crash(
            checkpoint,
            previous_generation=2,
            current_generation=3,
            status=kwargs.get("status", "WAIT"),
            error_code=kwargs.get("error_code"),
        )


def test_incompatible_checkpoint_schema_enters_safe_mode(tmp_path):
    database = _database(tmp_path)
    CheckpointStore(database).save(_checkpoint())
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "UPDATE checkpoints SET schema_version = 99 WHERE checkpoint_id LIKE 'run-1:%'"
        )
    safe_mode = _SafeMode()

    result = _coordinator(database, safe_mode=safe_mode).recover("run-1")

    assert result.status is RecoveryStatus.SAFE_MODE
    assert result.error_code == "CHECKPOINT_SCHEMA_INCOMPATIBLE"
    assert safe_mode.transitions == [("run-1", SafeModeReason.CHECKPOINT_INCOMPATIBLE)]


def test_corrupt_checkpoint_enters_safe_mode(tmp_path):
    database = _database(tmp_path)
    CheckpointStore(database).save(_checkpoint())
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "UPDATE checkpoints SET payload_json = '{not-json' WHERE checkpoint_id LIKE 'run-1:%'"
        )
    safe_mode = _SafeMode()

    result = _coordinator(database, safe_mode=safe_mode).recover("run-1")

    assert result.status is RecoveryStatus.SAFE_MODE
    assert result.error_code == "CHECKPOINT_UNAVAILABLE"
    assert safe_mode.transitions == [("run-1", SafeModeReason.CHECKPOINT_UNAVAILABLE)]


def test_incompatible_runtime_context_enters_safe_mode_without_reconciling(tmp_path):
    database = _database(tmp_path)
    action = {"step_id": "send", "tool_id": "foundation.send", "idempotency_key": "idem-1"}
    CheckpointStore(database).save(_checkpoint(pending_action=action))
    verifier = _Verifier()
    safe_mode = _SafeMode()

    result = _coordinator(
        database, verifier=verifier, compatible=False, safe_mode=safe_mode
    ).recover("run-1")

    assert result.status is RecoveryStatus.SAFE_MODE
    assert result.error_code == "WORKFLOW_CONTEXT_INCOMPATIBLE"
    assert verifier.calls == []
    assert safe_mode.transitions == [("run-1", SafeModeReason.CONTEXT_INCOMPATIBLE)]


def test_pending_approval_waits_without_replaying_action(tmp_path):
    database = _database(tmp_path)
    action = {"step_id": "approve", "tool_id": "foundation.write", "idempotency_key": "idem-2"}
    CheckpointStore(database).save(
        _checkpoint(pending_action=action, pending_approval={"approval_id": "approval-1"})
    )
    verifier = _Verifier()

    result = _coordinator(database, verifier=verifier).recover("run-1")

    assert result.status is RecoveryStatus.WAIT
    assert result.error_code == "APPROVAL_PENDING"
    assert verifier.calls == []


def test_checkpoint_without_pending_side_effect_is_resumable(tmp_path):
    database = _database(tmp_path)
    checkpoint = _checkpoint(sequence=4, state={"cursor": 4})
    CheckpointStore(database).save(checkpoint)

    result = _coordinator(database).recover("run-1")

    assert result.status is RecoveryStatus.RESUME
    assert result.checkpoint == checkpoint
