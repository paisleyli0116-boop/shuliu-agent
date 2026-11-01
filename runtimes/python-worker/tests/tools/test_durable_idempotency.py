from __future__ import annotations

import json

import pytest

from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.persistence.sqlite_idempotency import SQLiteIdempotencyStore
from shuliu_runtime.policy.rules import PolicyOutcome
from shuliu_runtime.tools.broker import IdentityPrincipal, ToolExecutionError
from shuliu_runtime.tools.idempotency import InMemoryIdempotencyStore

from tests.tools.test_broker_policy_order import _actor, _broker, _context


class SensitiveOutputExecutor:
    def __init__(self):
        self.calls = 0

    def execute(self, tool_id, arguments, context, idempotency_key):
        self.calls += 1
        return {
            "ok": True,
            "api_key": "do-not-persist-this-value",
            "evidence_ref": "evidence-write-1",
            "observation": "private page text must not be persisted",
        }

    def verify(self, tool_id, arguments, output, context):
        return False


def test_broker_replays_sanitized_result_after_sqlite_store_reopen(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    database = SQLiteDatabase(path)
    database.migrate()
    first_store = SQLiteIdempotencyStore(database)
    executor = SensitiveOutputExecutor()
    broker, _, _, _, _ = _broker(
        decision=PolicyOutcome.ALLOW,
        executor=executor,
        idempotency_store=first_store,
    )

    first = broker.execute(
        "foundation.document.write",
        {"document_id": "doc-1"},
        _actor(),
        _context(workflow_run_id="run-1", workflow_step_id="step-1"),
        idempotency_key="durable-replay",
    )

    reopened_store = SQLiteIdempotencyStore(SQLiteDatabase(path))
    second_executor = SensitiveOutputExecutor()
    replay_broker, _, _, _, _ = _broker(
        decision=PolicyOutcome.ALLOW,
        executor=second_executor,
        idempotency_store=reopened_store,
    )
    replay = replay_broker.execute(
        "foundation.document.write",
        {"document_id": "doc-1"},
        _actor(),
        _context(workflow_run_id="run-1", workflow_step_id="step-1"),
        idempotency_key="durable-replay",
    )

    with database.connect() as connection:
        stored_result = connection.execute(
            "SELECT result_json FROM tool_idempotency WHERE idempotency_key = ?",
            ("durable-replay",),
        ).fetchone()[0]

    assert first.output == replay.output
    assert first == replay
    assert "private page text must not be persisted" not in str(first.output)
    assert first.output == {"ok": True, "evidence_ref": "evidence-write-1"}
    assert executor.calls == 1
    assert second_executor.calls == 0
    assert "do-not-persist-this-value" not in stored_result
    persisted = json.loads(stored_result)
    assert "api_key" not in stored_result
    assert "private page text" not in stored_result
    assert persisted["output"] == {"ok": True, "evidence_ref": "evidence-write-1"}


def test_broker_fails_closed_when_local_durable_store_is_unavailable(tmp_path):
    store = SQLiteIdempotencyStore(SQLiteDatabase(tmp_path / "unmigrated.sqlite3"))
    executor = SensitiveOutputExecutor()
    broker, _, _, _, _ = _broker(
        decision=PolicyOutcome.ALLOW,
        executor=executor,
        idempotency_store=store,
    )

    with pytest.raises(ToolExecutionError) as unavailable:
        broker.execute(
            "foundation.document.write",
            {"document_id": "doc-1"},
            _actor(),
            _context(),
            idempotency_key="must-fail-closed",
        )

    assert unavailable.value.code == "IDEMPOTENCY_UNAVAILABLE"
    assert executor.calls == 0


def test_same_operation_key_cannot_dispatch_again_for_a_different_subject(tmp_path):
    class MultiSubjectIdentity:
        def resolve(self, actor):
            subject_id = actor.get("subject_id")
            if subject_id not in {"agent-1", "agent-2"}:
                return None
            return IdentityPrincipal(
                subject_id=subject_id,
                organization_id="org-1",
                permissions=("foundation.document.write",),
            )

    database = SQLiteDatabase(tmp_path / "subject-conflict.sqlite3")
    database.migrate()
    store = SQLiteIdempotencyStore(database)
    executor = SensitiveOutputExecutor()
    first_broker, _, _, _, _ = _broker(
        decision=PolicyOutcome.ALLOW,
        executor=executor,
        idempotency_store=store,
        identity=MultiSubjectIdentity(),
    )
    first_context = _context(workflow_run_id="same-run", workflow_step_id="same-step")
    first_broker.execute(
        "foundation.document.write",
        {"document_id": "doc-1"},
        _actor(),
        first_context,
        idempotency_key="same-operation-key",
    )

    second_broker, _, _, _, _ = _broker(
        decision=PolicyOutcome.ALLOW,
        executor=executor,
        idempotency_store=store,
        identity=MultiSubjectIdentity(),
    )
    with pytest.raises(ToolExecutionError) as conflict:
        second_broker.execute(
            "foundation.document.write",
            {"document_id": "doc-1"},
            {"subject_id": "agent-2", "organization_id": "org-1"},
            first_context,
            idempotency_key="same-operation-key",
        )

    assert conflict.value.code == "IDEMPOTENCY_KEY_CONFLICT"
    assert executor.calls == 1


def test_workflow_pair_and_explicit_operation_identity_do_not_collide():
    executor = SensitiveOutputExecutor()
    broker, _, _, _, _ = _broker(executor=executor)

    broker.execute(
        "foundation.document.write",
        {"document_id": "doc-1"},
        _actor(),
        _context(workflow_run_id="r", workflow_step_id="s"),
        idempotency_key="same-key",
    )
    broker.execute(
        "foundation.document.write",
        {"document_id": "doc-1"},
        _actor(),
        _context(operation_id="r:s"),
        idempotency_key="same-key",
    )

    assert executor.calls == 2


def test_broker_commits_dispatch_marker_before_calling_executor(tmp_path):
    database = SQLiteDatabase(tmp_path / "dispatch-order.sqlite3")
    database.migrate()

    class StateCheckingExecutor:
        dispatch_status = None

        def execute(self, tool_id, arguments, context, idempotency_key):
            with database.connect() as connection:
                row = connection.execute(
                    "SELECT status FROM tool_idempotency WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
            self.dispatch_status = row["status"]
            return {"ok": True, "evidence_ref": "evidence-dispatch-1"}

        def verify(self, tool_id, arguments, output, context):
            return True

    executor = StateCheckingExecutor()
    broker, _, _, _, _ = _broker(
        decision=PolicyOutcome.ALLOW,
        executor=executor,
        idempotency_store=SQLiteIdempotencyStore(database),
    )

    broker.execute(
        "foundation.document.write",
        {"document_id": "doc-1"},
        _actor(),
        _context(workflow_run_id="run-dispatch", workflow_step_id="step-1"),
        idempotency_key="dispatch-before-effect",
    )

    assert executor.dispatch_status == "DISPATCHED"


def test_broker_never_calls_executor_when_dispatch_marker_cannot_be_committed():
    class FailingDispatchStore(InMemoryIdempotencyStore):
        def mark_dispatched(self, **_kwargs):
            raise RuntimeError("test storage failure")

    executor = SensitiveOutputExecutor()
    broker, _, _, _, _ = _broker(
        decision=PolicyOutcome.ALLOW,
        executor=executor,
        idempotency_store=FailingDispatchStore(),
    )

    with pytest.raises(ToolExecutionError) as blocked:
        broker.execute(
            "foundation.document.write",
            {"document_id": "doc-1"},
            _actor(),
            _context(),
            idempotency_key="dispatch-write-failure",
        )

    assert blocked.value.code == "IDEMPOTENCY_UNAVAILABLE"
    assert executor.calls == 0


def test_missing_safe_result_reference_stays_unknown_and_cannot_be_replayed():
    class MissingReferenceExecutor:
        calls = 0

        def execute(self, tool_id, arguments, context, idempotency_key):
            self.calls += 1
            return {"ok": True}

        def verify(self, tool_id, arguments, output, context):
            return True

    executor = MissingReferenceExecutor()
    store = InMemoryIdempotencyStore()
    broker, _, _, _, _ = _broker(
        decision=PolicyOutcome.ALLOW,
        executor=executor,
        idempotency_store=store,
    )
    arguments = {"document_id": "doc-1"}
    context = _context(workflow_run_id="missing-ref", workflow_step_id="step-1")

    with pytest.raises(ToolExecutionError) as invalid:
        broker.execute(
            "foundation.document.write",
            arguments,
            _actor(),
            context,
            idempotency_key="missing-safe-ref",
        )
    assert invalid.value.code == "IDEMPOTENCY_RESULT_INVALID"

    with pytest.raises(ToolExecutionError) as unknown:
        broker.execute(
            "foundation.document.write",
            arguments,
            _actor(),
            context,
            idempotency_key="missing-safe-ref",
        )

    assert unknown.value.code == "RECONCILIATION_REQUIRED"
    assert executor.calls == 1
