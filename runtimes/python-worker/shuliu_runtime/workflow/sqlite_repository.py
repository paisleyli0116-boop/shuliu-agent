from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping

from shuliu_runtime.events.models import EventRecord, event_to_dict
from shuliu_runtime.persistence.outbox import canonical_json
from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.recovery.checkpoint import _insert_outbox_event

from .engine import (
    StepRun,
    StepRunStatus,
    WorkflowRepository,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowWaitReason,
)


class SQLiteWorkflowRepository(WorkflowRepository):
    """Durable WorkflowRun storage using the local DB and atomic event outbox."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def create(self, run: WorkflowRun, event: EventRecord) -> bool:
        _validate_run_event(run, event)
        payload = _encode_run(run)
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "INSERT OR IGNORE INTO workflow_runs "
                "(run_id, revision, payload_json, updated_at) VALUES (?, ?, ?, ?)",
                (run.run_id, run.revision, payload, run.updated_at.isoformat()),
            )
            if cursor.rowcount != 1:
                return False
            _insert_outbox_event(connection, event, f"workflow-event:{event.event_id}")
        return True

    def get(self, run_id: str) -> WorkflowRun | None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id is required")
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM workflow_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("persisted workflow run is invalid") from error
        if not isinstance(value, Mapping):
            raise ValueError("persisted workflow run is invalid")
        run = _decode_run(value)
        if run.run_id != run_id:
            raise ValueError("persisted workflow run identity is invalid")
        return run

    def list_unfinished(self) -> tuple[WorkflowRun, ...]:
        """Load every non-terminal run so recovery can fail closed on gaps."""
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT run_id, payload_json FROM workflow_runs ORDER BY updated_at, run_id"
            ).fetchall()
        runs: list[WorkflowRun] = []
        for row in rows:
            try:
                value = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError) as error:
                raise ValueError("persisted workflow run is invalid") from error
            if not isinstance(value, Mapping):
                raise ValueError("persisted workflow run is invalid")
            run = _decode_run(value)
            if run.run_id != row["run_id"]:
                raise ValueError("persisted workflow run identity is invalid")
            if run.status in {WorkflowRunStatus.RUNNING, WorkflowRunStatus.WAITING}:
                runs.append(run)
        return tuple(runs)

    def compare_and_set(
        self,
        *,
        run_id: str,
        expected_revision: int,
        updated_run: WorkflowRun,
        event: EventRecord,
    ) -> bool:
        if (
            not isinstance(run_id, str)
            or not run_id.strip()
            or isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
            or updated_run.run_id != run_id
            or updated_run.revision != expected_revision + 1
        ):
            raise ValueError("workflow compare-and-set transition is invalid")
        _validate_run_event(updated_run, event)
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE workflow_runs SET revision = ?, payload_json = ?, updated_at = ? "
                "WHERE run_id = ? AND revision = ?",
                (
                    updated_run.revision,
                    _encode_run(updated_run),
                    updated_run.updated_at.isoformat(),
                    run_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                return False
            _insert_outbox_event(connection, event, f"workflow-event:{event.event_id}")
        return True


def _validate_run_event(run: WorkflowRun, event: EventRecord) -> None:
    if not isinstance(run, WorkflowRun) or not isinstance(event, EventRecord):
        raise TypeError("workflow run and event are required")
    if (
        not run.run_id.strip()
        or event.organization_id != run.organization_id
        or event.trace_id != run.trace_id
    ):
        raise ValueError("workflow event scope does not match its run")
    event_to_dict(event)


def _encode_run(run: WorkflowRun) -> str:
    return canonical_json(
        {
            "run_id": run.run_id,
            "organization_id": run.organization_id,
            "trace_id": run.trace_id,
            "agent_run_id": run.agent_run_id,
            "workflow_id": run.workflow_id,
            "workflow_version_id": run.workflow_version_id,
            "definition_snapshot": run.definition_snapshot,
            "input_json": run.input_json,
            "run_context_json": run.run_context_json,
            "status": run.status.value,
            "current_step_id": run.current_step_id,
            "step_runs": [
                {
                    "step_id": step.step_id,
                    "status": step.status.value,
                    "attempts": step.attempts,
                    "retry_count": step.retry_count,
                    "input_json": step.input_json,
                    "output_json": step.output_json,
                    "error_code": step.error_code,
                    "wait_reason": step.wait_reason.value if step.wait_reason else None,
                    "started_at": step.started_at.isoformat(),
                    "completed_at": step.completed_at.isoformat() if step.completed_at else None,
                }
                for step in run.step_runs
            ],
            "resume_token": run.resume_token,
            "resume_token_hash": run.resume_token_hash,
            "wait_reason": run.wait_reason.value if run.wait_reason else None,
            "revision": run.revision,
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
        }
    )


def _decode_run(value: Mapping[str, Any]) -> WorkflowRun:
    try:
        raw_steps = value["step_runs"]
        if not isinstance(raw_steps, list):
            raise ValueError
        steps = tuple(
            StepRun(
                step_id=_text(item["step_id"]),
                status=StepRunStatus(_text(item["status"])),
                attempts=_non_negative_int(item["attempts"]),
                retry_count=_non_negative_int(item["retry_count"]),
                input_json=_optional_json_text(item["input_json"]),
                output_json=_optional_json_text(item["output_json"]),
                error_code=_optional_text(item["error_code"]),
                wait_reason=(
                    WorkflowWaitReason(_text(item["wait_reason"]))
                    if item["wait_reason"] is not None
                    else None
                ),
                started_at=_datetime(item["started_at"]),
                completed_at=(
                    _datetime(item["completed_at"])
                    if item["completed_at"] is not None
                    else None
                ),
            )
            for item in raw_steps
            if isinstance(item, Mapping)
        )
        if len(steps) != len(raw_steps):
            raise ValueError
        return WorkflowRun(
            run_id=_text(value["run_id"]),
            organization_id=_text(value["organization_id"]),
            trace_id=_text(value["trace_id"]),
            agent_run_id=_optional_text(value["agent_run_id"]),
            workflow_id=_text(value["workflow_id"]),
            workflow_version_id=_text(value["workflow_version_id"]),
            definition_snapshot=_text(value["definition_snapshot"]),
            input_json=_text(value["input_json"]),
            run_context_json=_text(value["run_context_json"]),
            status=WorkflowRunStatus(_text(value["status"])),
            current_step_id=_optional_text(value["current_step_id"]),
            step_runs=steps,
            resume_token=_optional_text(value["resume_token"]),
            resume_token_hash=_optional_text(value["resume_token_hash"]),
            wait_reason=(
                WorkflowWaitReason(_text(value["wait_reason"]))
                if value["wait_reason"] is not None
                else None
            ),
            revision=_non_negative_int(value["revision"]),
            created_at=_datetime(value["created_at"]),
            updated_at=_datetime(value["updated_at"]),
        )
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError("persisted workflow run is invalid") from error


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _text(value)


def _optional_json_text(value: object) -> str | None:
    candidate = _optional_text(value)
    if candidate is not None:
        decoded = json.loads(candidate)
        if not _json_value(decoded):
            raise ValueError
    return candidate


def _json_value(value: object) -> bool:
    try:
        json.dumps(value, allow_nan=False)
        return True
    except (TypeError, ValueError):
        return False


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError
    return value


def _datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError
    return result
