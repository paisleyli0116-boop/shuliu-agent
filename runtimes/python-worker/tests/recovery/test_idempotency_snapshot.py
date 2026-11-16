from __future__ import annotations

from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.persistence.sqlite_idempotency import SQLiteIdempotencyStore
from shuliu_runtime.recovery.idempotency_state import IdempotencyExternalStateReader
from shuliu_runtime.recovery.checkpoint import Checkpoint, CheckpointStore
from shuliu_runtime.recovery.process_recovery import ProcessCrashRecovery
from shuliu_runtime.recovery.reconciler import (
    ActionReconciliationStatus,
    RecoveryCoordinator,
    RecoveryStatus,
)
from shuliu_runtime.tools.idempotency import (
    IdempotencyRecord,
    IdempotencyRecordStatus,
)


_SCOPE = ("org-1", "tool-1", "operation-1")
_KEY = "operation-key-1"
_DIGEST = "sha256:request-1"


def _store(tmp_path):
    database = SQLiteDatabase(tmp_path / "idempotency-inspection.sqlite3")
    database.migrate()
    return database, SQLiteIdempotencyStore(database)


def test_inspect_returns_absent_without_creating_or_mutating_a_claim(tmp_path):
    database, store = _store(tmp_path)

    snapshot = store.inspect(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)

    assert snapshot.status is IdempotencyRecordStatus.ABSENT
    assert snapshot.result is None
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM tool_idempotency").fetchone()[0] == 0


def test_inspect_classifies_claim_lifecycle_without_mutating_it(tmp_path):
    database, store = _store(tmp_path)
    store.begin(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    claimed = store.inspect(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    assert claimed.status is IdempotencyRecordStatus.CLAIMED

    store.mark_dispatched(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    dispatched = store.inspect(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    assert dispatched.status is IdempotencyRecordStatus.DISPATCHED

    store.mark_indeterminate(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    indeterminate = store.inspect(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    assert indeterminate.status is IdempotencyRecordStatus.INDETERMINATE

    with database.connect() as connection:
        row = connection.execute(
            "SELECT status, result_json FROM tool_idempotency WHERE idempotency_key = ?",
            (_KEY,),
        ).fetchone()
    assert tuple(row) == ("INDETERMINATE", None)


def test_inspect_returns_only_the_sanitized_completed_result(tmp_path):
    _, store = _store(tmp_path)
    store.begin(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    store.mark_dispatched(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    store.complete(
        scope=_SCOPE,
        key=_KEY,
        request_digest=_DIGEST,
        result={
            "tool_id": "tool-1",
            "tool_version": "1.0.0",
            "execution_status": "SUCCEEDED",
            "verification_status": "VERIFIED",
            "output": {
                "result_ref": "evidence-1",
                "status": "SUCCEEDED",
                "private_payload": "must-not-be-persisted",
            },
            "error_code": None,
            "policy_decision": {},
            "policy_version": "policy-v1",
            "policy_rule_id": None,
        },
    )

    snapshot = store.inspect(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)

    assert snapshot.status is IdempotencyRecordStatus.COMPLETED
    assert snapshot.result["verification_status"] == "VERIFIED"
    assert snapshot.result["output"] == {
        "result_ref": "evidence-1",
        "status": "SUCCEEDED",
    }
    assert "private_payload" not in repr(snapshot.result)


def test_inspect_reports_digest_conflict_without_returning_stored_result(tmp_path):
    _, store = _store(tmp_path)
    store.begin(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    store.mark_dispatched(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    store.complete(
        scope=_SCOPE,
        key=_KEY,
        request_digest=_DIGEST,
        result={"result_ref": "evidence-1"},
    )

    snapshot = store.inspect(
        scope=_SCOPE, key=_KEY, request_digest="sha256:different"
    )

    assert snapshot.status is IdempotencyRecordStatus.CONFLICT
    assert snapshot.result is None


def test_recovery_reader_classifies_missing_idempotency_as_not_applied(tmp_path):
    _, store = _store(tmp_path)
    reader = IdempotencyExternalStateReader(store)

    result = reader.inspect("run-1", _reader_action())

    assert result.status is ActionReconciliationStatus.NOT_APPLIED
    assert result.verified_output is None


def test_recovery_reader_keeps_claimed_or_dispatched_states_unknown(tmp_path):
    _, store = _store(tmp_path)
    reader = IdempotencyExternalStateReader(store)
    store.begin(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    claimed = reader.inspect("run-1", _reader_action())
    assert claimed.status is ActionReconciliationStatus.UNKNOWN

    store.mark_dispatched(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    dispatched = reader.inspect("run-1", _reader_action())
    assert dispatched.status is ActionReconciliationStatus.UNKNOWN

    store.mark_indeterminate(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    indeterminate = reader.inspect("run-1", _reader_action())
    assert indeterminate.status is ActionReconciliationStatus.UNKNOWN


def test_recovery_reader_returns_only_a_verified_persisted_output(tmp_path):
    _, store = _store(tmp_path)
    store.begin(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    store.mark_dispatched(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    store.complete(
        scope=_SCOPE,
        key=_KEY,
        request_digest=_DIGEST,
        result={
            "tool_id": "tool-1",
            "tool_version": "1.0.0",
            "execution_status": "SUCCEEDED",
            "verification_status": "VERIFIED",
            "output": {
                "result_ref": "evidence-1",
                "status": "SUCCEEDED",
                "private_payload": "must-not-be-persisted",
            },
            "error_code": None,
            "policy_decision": {},
            "policy_version": "policy-v1",
            "policy_rule_id": None,
        },
    )
    reader = IdempotencyExternalStateReader(store)

    result = reader.inspect("run-1", _reader_action())

    assert result.status is ActionReconciliationStatus.APPLIED
    assert result.verified_output == {
        "result_ref": "evidence-1",
        "status": "SUCCEEDED",
    }


def test_recovery_reader_fails_closed_on_bad_scope_or_unverified_result():
    class Inspector:
        def __init__(self, record):
            self.record = record
            self.calls = 0

        def inspect(self, **_kwargs):
            self.calls += 1
            return self.record

    inspector = Inspector(
        IdempotencyRecord(
            IdempotencyRecordStatus.COMPLETED,
            {
                "execution_status": "SUCCEEDED",
                "verification_status": "UNVERIFIED",
                "output": {"result_ref": "evidence-1"},
            },
        )
    )
    reader = IdempotencyExternalStateReader(inspector)

    malformed = reader.inspect("run-1", {"idempotency_key": _KEY})
    assert malformed.status is ActionReconciliationStatus.UNKNOWN
    assert inspector.calls == 0

    unverified = reader.inspect("run-1", _reader_action())
    assert unverified.status is ActionReconciliationStatus.UNKNOWN
    assert unverified.verified_output is None


def test_recovery_reader_reprojects_persisted_output_before_checkpointing():
    class Inspector:
        def inspect(self, **_kwargs):
            return IdempotencyRecord(
                IdempotencyRecordStatus.COMPLETED,
                {
                    "execution_status": "SUCCEEDED",
                    "verification_status": "VERIFIED",
                    "output": {
                        "result_ref": "evidence-1",
                        "status": "SUCCEEDED",
                        "private_payload": "must-not-enter-checkpoint",
                    },
                },
            )

    reader = IdempotencyExternalStateReader(Inspector())

    result = reader.inspect("run-1", _reader_action())

    assert result.status is ActionReconciliationStatus.APPLIED
    assert result.verified_output == {
        "result_ref": "evidence-1",
        "status": "SUCCEEDED",
    }


def test_process_recovery_does_not_treat_unexecuted_recovery_as_success(tmp_path):
    database, store = _store(tmp_path)
    checkpoint_store = CheckpointStore(database)
    checkpoint_store.save(_pending_checkpoint())
    store.begin(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    store.mark_dispatched(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    reader = IdempotencyExternalStateReader(store)
    recovery = RecoveryCoordinator(
        checkpoint_store=checkpoint_store,
        external_state=reader,
        version_validator=_CompatibleVersions(),
        safe_mode=_SafeMode(),
    )
    process_recovery = ProcessCrashRecovery(
        recovery=recovery,
        checkpoint_store=checkpoint_store,
        fence=_ConfirmedFence(),
        events=checkpoint_store,
    )

    decision = process_recovery.recover_process(
        "run-1", previous_generation=1, current_generation=2
    )

    assert decision.status is RecoveryStatus.WAIT
    assert decision.error_code == "SIDE_EFFECT_STATE_UNKNOWN"
    assert checkpoint_store.latest("run-1").sequence == 1


def test_process_recovery_reconciles_completed_result_without_replaying(tmp_path):
    database, store = _store(tmp_path)
    checkpoint_store = CheckpointStore(database)
    checkpoint_store.save(_pending_checkpoint())
    store.begin(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    store.mark_dispatched(scope=_SCOPE, key=_KEY, request_digest=_DIGEST)
    store.complete(
        scope=_SCOPE,
        key=_KEY,
        request_digest=_DIGEST,
        result={
            "tool_id": "tool-1",
            "tool_version": "1.0.0",
            "execution_status": "SUCCEEDED",
            "verification_status": "VERIFIED",
            "output": {"evidence_ref": "evidence-1", "status": "SUCCEEDED"},
            "error_code": None,
            "policy_decision": {},
            "policy_version": "policy-v1",
            "policy_rule_id": None,
        },
    )
    recovery = RecoveryCoordinator(
        checkpoint_store=checkpoint_store,
        external_state=IdempotencyExternalStateReader(store),
        version_validator=_CompatibleVersions(),
        safe_mode=_SafeMode(),
    )
    process_recovery = ProcessCrashRecovery(
        recovery=recovery,
        checkpoint_store=checkpoint_store,
        fence=_ConfirmedFence(),
        events=checkpoint_store,
    )

    decision = process_recovery.recover_process(
        "run-1", previous_generation=1, current_generation=2
    )

    assert decision.status is RecoveryStatus.RESUME
    assert decision.checkpoint.sequence == 2
    assert decision.checkpoint.pending_action is None
    assert decision.checkpoint.state["reconciled_outputs"]["step-1"] == {
        "evidence_ref": "evidence-1",
        "status": "SUCCEEDED",
    }


def _pending_action():
    return {
        "step_id": "step-1",
        "workflow_run_id": "run-1",
        "tool_id": "tool-1",
        "organization_id": "forged-org",
        "operation_id": "operation-1",
        "idempotency_key": _KEY,
        "request_digest": _DIGEST,
    }


def _reader_action():
    return {**_pending_action(), "organization_id": "org-1"}


def _pending_checkpoint():
    from datetime import UTC, datetime

    from shuliu_runtime.recovery.checkpoint import Checkpoint

    return Checkpoint(
        run_id="run-1",
        sequence=1,
        state={},
        last_completed_step=None,
        pending_action=_pending_action(),
        pending_approval=None,
        context_version="workflow:v1",
        created_at=datetime(2026, 11, 2, tzinfo=UTC),
        organization_id="org-1",
        trace_id="trace-1",
    )


class _CompatibleVersions:
    def is_compatible(self, _checkpoint):
        return True


class _SafeMode:
    def enter(self, **_kwargs):
        raise AssertionError("compatible, fenced recovery must not enter Safe Mode")


class _ConfirmedFence:
    def fence_previous(self, _run_id, *, previous_generation, current_generation):
        return previous_generation == 1 and current_generation == 2
