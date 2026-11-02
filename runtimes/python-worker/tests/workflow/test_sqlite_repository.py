from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from shuliu_runtime.events.models import EventRecord
from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.policy.rules import PolicyOutcome
from shuliu_runtime.tools.broker import (
    ToolExecutionStatus,
    ToolResult,
    VerificationStatus,
)
from shuliu_runtime.workflow.definition import WorkflowDefinition
from shuliu_runtime.workflow.engine import (
    StepRun,
    StepRunStatus,
    WorkflowEngine,
    WorkflowRun,
    WorkflowRunStatus,
)
from shuliu_runtime.workflow.sqlite_repository import SQLiteWorkflowRepository
from shuliu_runtime.ipc.bootstrap import bootstrap_runtime


def _run(run_id: str = "run-1", revision: int = 0) -> WorkflowRun:
    now = datetime.now(UTC)
    return WorkflowRun(
        run_id=run_id,
        organization_id="org-1",
        trace_id="trace-1",
        agent_run_id=None,
        workflow_id="wf-1",
        workflow_version_id="wf-v1",
        definition_snapshot='{"workflow_id":"wf-1"}',
        input_json='{"value":"safe"}',
        run_context_json='{"organization_id":"org-1","trace_id":"trace-1"}',
        status=WorkflowRunStatus.RUNNING,
        current_step_id="step-1",
        step_runs=(
            StepRun(
                step_id="step-1",
                status=StepRunStatus.RUNNING,
                attempts=1,
                retry_count=0,
                input_json='{"value":"safe"}',
                output_json=None,
                error_code=None,
                wait_reason=None,
                started_at=now,
            ),
        ),
        resume_token=None,
        resume_token_hash=None,
        wait_reason=None,
        revision=revision,
        created_at=now,
        updated_at=now,
    )


def _event(event_id: str, run: WorkflowRun) -> EventRecord:
    return EventRecord(
        event_id=event_id,
        schema_version=1,
        timestamp=run.updated_at.isoformat(),
        organization_id=run.organization_id,
        trace_id=run.trace_id,
        event_type="WORKFLOW_RUN_CREATED",
        payload={"workflow_run_id": run.run_id},
    )


def test_sqlite_repository_persists_run_and_event_atomically_across_reopen(tmp_path):
    database_path = tmp_path / "runtime.sqlite"
    database = SQLiteDatabase(database_path)
    database.migrate()
    repository = SQLiteWorkflowRepository(database)
    run = _run()

    assert repository.create(run, _event("event-1", run)) is True
    assert repository.get(run.run_id) == run

    reopened = SQLiteWorkflowRepository(SQLiteDatabase(database_path))
    assert reopened.get(run.run_id) == run
    with SQLiteDatabase(database_path).connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1


def test_sqlite_repository_rejects_duplicate_run_without_adding_event(tmp_path):
    database = SQLiteDatabase(tmp_path / "runtime.sqlite")
    database.migrate()
    repository = SQLiteWorkflowRepository(database)
    run = _run()

    assert repository.create(run, _event("event-1", run)) is True
    assert repository.create(run, _event("event-2", run)) is False
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1


def test_sqlite_repository_rejects_unfinished_payload_with_mismatched_row_identity(tmp_path):
    database = SQLiteDatabase(tmp_path / "runtime.sqlite")
    database.migrate()
    repository = SQLiteWorkflowRepository(database)
    run = _run("row-run-id")
    assert repository.create(run, _event("event-row-run-id", run)) is True

    with database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM workflow_runs WHERE run_id = ?", (run.run_id,)
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload["run_id"] = "different-payload-run-id"
        connection.execute(
            "UPDATE workflow_runs SET payload_json = ? WHERE run_id = ?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), run.run_id),
        )

    with pytest.raises(ValueError, match="identity is invalid"):
        repository.list_unfinished()


def test_sqlite_repository_compare_and_set_requires_current_revision(tmp_path):
    database = SQLiteDatabase(tmp_path / "runtime.sqlite")
    database.migrate()
    repository = SQLiteWorkflowRepository(database)
    run = _run()
    assert repository.create(run, _event("event-1", run)) is True
    updated = _run(revision=1)

    assert repository.compare_and_set(
        run_id=run.run_id,
        expected_revision=0,
        updated_run=updated,
        event=_event("event-2", updated),
    ) is True
    assert repository.compare_and_set(
        run_id=run.run_id,
        expected_revision=0,
        updated_run=_run(revision=1),
        event=_event("event-3", updated),
    ) is False
    assert repository.get(run.run_id) == updated
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 2


def test_sqlite_repository_rolls_back_run_update_when_event_append_conflicts(tmp_path):
    database = SQLiteDatabase(tmp_path / "runtime.sqlite")
    database.migrate()
    repository = SQLiteWorkflowRepository(database)
    run = _run()
    assert repository.create(run, _event("event-1", run)) is True
    updated = _run(revision=1)

    with pytest.raises(Exception):
        repository.compare_and_set(
            run_id=run.run_id,
            expected_revision=0,
            updated_run=updated,
            event=_event("event-1", updated),
        )

    assert repository.get(run.run_id) == run
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1


def test_production_bootstrap_repository_persists_actual_workflow_engine_transitions(tmp_path):
    bootstrap = bootstrap_runtime(tmp_path)

    class Broker:
        def execute(self, tool_id, args, actor, context, *, idempotency_key):
            assert tool_id == "foundation.test"
            assert idempotency_key
            return ToolResult(
                tool_id=tool_id,
                tool_version="1.0.0",
                execution_status=ToolExecutionStatus.SUCCEEDED,
                verification_status=VerificationStatus.VERIFIED,
                output={"accepted": args["value"] == "safe"},
                error_code=None,
                policy_decision=PolicyOutcome.ALLOW,
                policy_version="test-policy-v1",
                policy_rule_id="test-allow",
            )

    definition = WorkflowDefinition.from_dict(
        {
            "workflow_id": "production-repository-smoke",
            "workflow_version_id": "v1",
            "input_schema": {
                "type": "object",
                "required": ["value"],
                "properties": {"value": {"type": "string"}},
                "additionalProperties": False,
            },
            "steps": [
                {
                    "id": "call",
                    "type": "tool",
                    "tool_id": "foundation.test",
                    "arguments": {"value": {"$ref": "input.value"}},
                    "next": "END",
                }
            ],
        }
    )
    engine = WorkflowEngine(repository=bootstrap.workflow_repository, tool_broker=Broker())
    run = engine.start_workflow(
        definition,
        {"value": "safe"},
        {
            "organization_id": "org-local",
            "trace_id": "trace-local-workflow",
            "actor": {
                "subject_id": "local-user",
                "organization_id": "org-local",
                "permissions": ["foundation.test"],
            },
        },
    )

    reopened = SQLiteWorkflowRepository(SQLiteDatabase(bootstrap.database_path))
    assert run.status is WorkflowRunStatus.SUCCEEDED
    assert reopened.get(run.run_id) == run
    with bootstrap.database.connect() as connection:
        event_types = [row[0] for row in connection.execute(
            "SELECT json_extract(payload_json, '$.event_type') FROM outbox ORDER BY created_at"
        )]
    assert "WORKFLOW_RUN_CREATED" in event_types
    assert "WORKFLOW_RUN_SUCCEEDED" in event_types
