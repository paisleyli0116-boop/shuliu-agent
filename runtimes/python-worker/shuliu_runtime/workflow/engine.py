from __future__ import annotations

import hmac
import json
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from typing import Protocol, cast
from uuid import uuid4

from shuliu_runtime.events.models import EventRecord, event_from_dict
from shuliu_runtime.tools.broker import (
    ToolExecutionError,
    ToolResult,
    VerificationStatus,
)

from .conditions import WorkflowValueError, evaluate_condition, resolve_references
from .definition import WorkflowDefinition


class WorkflowRunStatus(str, Enum):
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class StepRunStatus(str, Enum):
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class WorkflowWaitReason(str, Enum):
    APPROVAL = "APPROVAL"
    EXTERNAL = "EXTERNAL"


class WorkflowEngineError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WorkflowStateConflict(WorkflowEngineError):
    def __init__(self, message: str = "Workflow state changed concurrently") -> None:
        super().__init__("WORKFLOW_STATE_CONFLICT", message)


class WorkflowNotFound(WorkflowEngineError):
    def __init__(self, run_id: str) -> None:
        super().__init__(
            "WORKFLOW_RUN_NOT_FOUND", f"WorkflowRun {run_id} was not found"
        )


class WorkflowResumeRejected(WorkflowEngineError):
    def __init__(self) -> None:
        super().__init__("WORKFLOW_RESUME_REJECTED", "Workflow resume token is invalid")


class WorkflowStepError(WorkflowEngineError):
    """A step adapter error with explicit retry, fallback, and resume safety."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        fallback_safe: bool = False,
        resumable: bool = False,
    ) -> None:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("workflow step error code is required")
        if any(
            not isinstance(value, bool)
            for value in (retryable, fallback_safe, resumable)
        ):
            raise TypeError("workflow step safety flags must be booleans")
        super().__init__(code, code)
        self.retryable = retryable
        self.fallback_safe = fallback_safe
        self.resumable = resumable


@dataclass(frozen=True)
class StepRun:
    step_id: str
    status: StepRunStatus
    attempts: int
    retry_count: int
    input_json: str | None
    output_json: str | None
    error_code: str | None
    wait_reason: WorkflowWaitReason | None
    started_at: datetime
    completed_at: datetime | None = None

    @property
    def input(self) -> object | None:
        return _decode_json(self.input_json) if self.input_json is not None else None

    @property
    def output(self) -> object | None:
        return _decode_json(self.output_json) if self.output_json is not None else None


@dataclass(frozen=True)
class WorkflowRun:
    run_id: str
    organization_id: str
    trace_id: str
    agent_run_id: str | None
    workflow_id: str
    workflow_version_id: str
    definition_snapshot: str
    input_json: str
    run_context_json: str
    status: WorkflowRunStatus
    current_step_id: str | None
    step_runs: tuple[StepRun, ...]
    resume_token: str | None
    resume_token_hash: str | None
    wait_reason: WorkflowWaitReason | None
    revision: int
    created_at: datetime
    updated_at: datetime

    @property
    def input(self) -> object:
        return _decode_json(self.input_json)


class ToolBrokerPort(Protocol):
    """Only supported path for Tool invocation; adapters must preserve Broker gates."""

    def execute(
        self,
        tool_id: str,
        args: Mapping[str, object],
        actor: Mapping[str, object],
        context: Mapping[str, object],
        *,
        idempotency_key: str | None,
    ) -> ToolResult: ...


class WorkflowRepository(Protocol):
    """Persistence port; each WorkflowRun mutation and Event must commit atomically."""

    def create(self, run: WorkflowRun, event: EventRecord) -> bool: ...

    def get(self, run_id: str) -> WorkflowRun | None: ...

    def compare_and_set(
        self,
        *,
        run_id: str,
        expected_revision: int,
        updated_run: WorkflowRun,
        event: EventRecord,
    ) -> bool: ...


Clock = Callable[[], datetime]


class WorkflowEngine:
    """Executes immutable, business-neutral Workflow definitions."""

    def __init__(
        self,
        *,
        repository: WorkflowRepository,
        tool_broker: ToolBrokerPort,
        clock: Clock | None = None,
    ) -> None:
        if not callable(getattr(tool_broker, "execute", None)):
            raise TypeError("tool_broker must provide execute")
        self._repository = repository
        self._tool_broker = tool_broker
        self._clock = clock or (lambda: datetime.now(UTC))

    def start_workflow(
        self,
        definition_version: WorkflowDefinition,
        input: Mapping[str, object],
        run_context: Mapping[str, object],
    ) -> WorkflowRun:
        if not isinstance(definition_version, WorkflowDefinition):
            raise TypeError("definition_version must be a validated WorkflowDefinition")
        if not isinstance(input, Mapping):
            raise TypeError("workflow input must be an object")
        if not definition_version.validate_input(input):
            raise ValueError("workflow input does not match its versioned input schema")
        if not isinstance(run_context, Mapping):
            raise TypeError("workflow run_context must be an object")
        organization_id = _required_text(run_context, "organization_id")
        trace_id = _required_text(run_context, "trace_id")
        input_json = _canonical_json(input, "workflow input")
        context_json = _canonical_json(run_context, "workflow run_context")
        now = self._now()
        definition = definition_version.to_dict()
        first_step = cast(dict[str, object], cast(list[object], definition["steps"])[0])
        run = WorkflowRun(
            run_id=str(uuid4()),
            organization_id=organization_id,
            trace_id=trace_id,
            agent_run_id=_optional_text(run_context.get("agent_run_id")),
            workflow_id=definition_version.workflow_id,
            workflow_version_id=definition_version.workflow_version_id,
            definition_snapshot=definition_version.canonical_json,
            input_json=input_json,
            run_context_json=context_json,
            status=WorkflowRunStatus.RUNNING,
            current_step_id=cast(str, first_step["id"]),
            step_runs=(),
            resume_token=None,
            resume_token_hash=None,
            wait_reason=None,
            revision=0,
            created_at=now,
            updated_at=now,
        )
        event = _event(
            run,
            "WORKFLOW_RUN_CREATED",
            now,
            {
                "workflow_id": run.workflow_id,
                "workflow_version_id": run.workflow_version_id,
            },
        )
        if not self._repository.create(run, event):
            raise WorkflowStateConflict("WorkflowRun ID already exists")
        return self._advance(run)

    def resume_workflow(self, run_id: str, resume_token: str) -> WorkflowRun:
        if not _non_empty(run_id) or not _non_empty(resume_token):
            raise WorkflowResumeRejected()
        run = self._repository.get(run_id)
        if run is None:
            raise WorkflowNotFound(run_id)
        if (
            run.status is not WorkflowRunStatus.WAITING
            or run.resume_token_hash is None
            or not hmac.compare_digest(
                run.resume_token_hash, _resume_token_hash(resume_token)
            )
        ):
            raise WorkflowResumeRejected()
        now = self._now()
        resumed = replace(
            run,
            status=WorkflowRunStatus.RUNNING,
            resume_token=None,
            resume_token_hash=None,
            wait_reason=None,
            revision=run.revision + 1,
            updated_at=now,
        )
        event = _event(
            resumed,
            "WORKFLOW_RUN_RESUMED",
            now,
            {
                "workflow_id": resumed.workflow_id,
                "workflow_version_id": resumed.workflow_version_id,
            },
        )
        if not self._repository.compare_and_set(
            run_id=run.run_id,
            expected_revision=run.revision,
            updated_run=resumed,
            event=event,
        ):
            raise WorkflowStateConflict()
        return self._advance(resumed)

    def _advance(self, run: WorkflowRun) -> WorkflowRun:
        definition = WorkflowDefinition.from_dict(
            cast(dict[str, object], json.loads(run.definition_snapshot))
        ).to_dict()
        steps = cast(list[dict[str, object]], definition["steps"])
        by_id = {cast(str, step["id"]): step for step in steps}
        positions = {cast(str, step["id"]): index for index, step in enumerate(steps)}

        while run.status is WorkflowRunStatus.RUNNING:
            step_id = run.current_step_id
            if step_id is None:
                return self._finish_run(run, WorkflowRunStatus.SUCCEEDED, None)
            step = by_id[step_id]
            if step["type"] == "condition":
                run = self._run_condition(run, step, positions)
                continue
            run = self._run_tool_step(run, step, positions)
        return run

    def _run_condition(
        self,
        run: WorkflowRun,
        step: dict[str, object],
        positions: Mapping[str, int],
    ) -> WorkflowRun:
        step_id = cast(str, step["id"])
        now = self._now()
        run = self._start_step(run, step_id, None, now)
        step_run = self._step_run(run, step_id)
        variables = self._variables(run)
        try:
            matched = evaluate_condition(
                cast(Mapping[str, object], step["condition"]), variables
            )
        except WorkflowValueError:
            return self._fail_step(run, step_id, "WORKFLOW_CONDITION_INVALID")
        target = cast(str, step["then"] if matched else step["else"])
        output_json = _canonical_json({"matched": matched}, "condition result")
        completed = replace(
            step_run,
            status=StepRunStatus.SUCCEEDED,
            output_json=output_json,
            error_code=None,
            completed_at=self._now(),
        )
        next_step_id = self._next_step(step, target, positions)
        return self._complete_step(run, completed, next_step_id)

    def _run_tool_step(
        self,
        run: WorkflowRun,
        step: dict[str, object],
        positions: Mapping[str, int],
    ) -> WorkflowRun:
        step_id = cast(str, step["id"])
        try:
            arguments = resolve_references(step["arguments"], self._variables(run))
        except WorkflowValueError:
            return self._fail_step(run, step_id, "WORKFLOW_REFERENCE_INVALID")
        if not isinstance(arguments, Mapping):
            return self._fail_step(run, step_id, "WORKFLOW_TOOL_ARGUMENTS_INVALID")
        context = cast(dict[str, object], _decode_json(run.run_context_json))
        actor = context.get("actor")
        if not isinstance(actor, Mapping):
            return self._fail_step(run, step_id, "IDENTITY_REQUIRED")
        broker_context = dict(context)
        broker_context["workflow_run_id"] = run.run_id
        broker_context["workflow_step_id"] = step_id
        run = self._start_step(run, step_id, arguments, self._now())
        step_run = self._step_run(run, step_id)
        tool_id = cast(str, step["tool_id"])
        idempotency_key = _step_idempotency_key(run, step_id)
        try:
            result = self._tool_broker.execute(
                tool_id,
                cast(Mapping[str, object], arguments),
                actor,
                broker_context,
                idempotency_key=idempotency_key,
            )
        except ToolExecutionError as error:
            if error.code == "APPROVAL_NOT_CONSUMABLE":
                return self._wait_for_resume(
                    run,
                    step_id,
                    WorkflowWaitReason.APPROVAL,
                    error.code,
                )
            return self._fail_step(run, step_id, error.code)
        except WorkflowStepError as error:
            return self._handle_step_error(run, step, error)
        except Exception:  # noqa: BLE001 - fail closed and never retry unknown Tool errors
            return self._fail_step(run, step_id, "WORKFLOW_STEP_FAILED")

        if not isinstance(result, ToolResult):
            return self._fail_step(run, step_id, "WORKFLOW_TOOL_RESULT_INVALID")
        if result.verification_status is not VerificationStatus.VERIFIED:
            return self._fail_step(
                run,
                step_id,
                result.error_code or "TOOL_RESULT_UNVERIFIED",
            )
        try:
            output_json = _canonical_json(result.output, "Tool output")
        except ValueError:
            return self._fail_step(run, step_id, "WORKFLOW_TOOL_RESULT_INVALID")
        completed = replace(
            step_run,
            status=StepRunStatus.SUCCEEDED,
            output_json=output_json,
            error_code=None,
            completed_at=self._now(),
        )
        next_step_id = self._next_step(step, None, positions)
        return self._complete_step(run, completed, next_step_id)

    def _start_step(
        self,
        run: WorkflowRun,
        step_id: str,
        step_input: object | None,
        now: datetime,
    ) -> WorkflowRun:
        previous = next(
            (item for item in run.step_runs if item.step_id == step_id), None
        )
        step_run = StepRun(
            step_id=step_id,
            status=StepRunStatus.RUNNING,
            attempts=(previous.attempts + 1) if previous else 1,
            retry_count=previous.retry_count if previous else 0,
            input_json=(
                _canonical_json(step_input, "step input")
                if step_input is not None
                else (previous.input_json if previous else None)
            ),
            output_json=None,
            error_code=None,
            wait_reason=None,
            started_at=previous.started_at if previous else now,
        )
        updated = self._replace_step(run, step_run)
        persisted = self._persist_run(
            run,
            updated,
            "WORKFLOW_STEP_STATE_CHANGED",
            {
                "step_id": step_id,
                "step_status": step_run.status.value,
                "attempts": step_run.attempts,
            },
        )
        return persisted

    def _handle_step_error(
        self,
        run: WorkflowRun,
        step: dict[str, object],
        error: WorkflowStepError,
    ) -> WorkflowRun:
        step_run = self._step_run(run, cast(str, step["id"]))
        retry = cast(dict[str, object], step.get("retry", {}))
        max_retries = cast(int, retry.get("max_retries", 0))
        if error.retryable and step_run.retry_count < max_retries:
            retried = replace(
                step_run, retry_count=step_run.retry_count + 1, error_code=error.code
            )
            return self._persist_run(
                run,
                self._replace_step(run, retried),
                "WORKFLOW_STEP_RETRY_SCHEDULED",
                {
                    "step_id": step_run.step_id,
                    "retry_count": retried.retry_count,
                    "error_code": error.code,
                },
            )

        failure = cast(dict[str, object], step.get("on_failure", {"action": "fail"}))
        action = cast(str, failure["action"])
        if action == "pause" and error.resumable:
            return self._wait_for_resume(
                run,
                step_run.step_id,
                WorkflowWaitReason.EXTERNAL,
                error.code,
            )
        if action == "fallback" and error.fallback_safe:
            failed = replace(
                step_run,
                status=StepRunStatus.FAILED,
                error_code=error.code,
                completed_at=self._now(),
            )
            target = cast(str, failure["step_id"])
            return self._complete_step(run, failed, target)
        return self._fail_step(run, step_run.step_id, error.code)

    def _wait_for_resume(
        self,
        run: WorkflowRun,
        step_id: str,
        reason: WorkflowWaitReason,
        error_code: str,
    ) -> WorkflowRun:
        token = secrets.token_urlsafe(32)
        step_run = replace(
            self._step_run(run, step_id),
            status=StepRunStatus.WAITING,
            error_code=error_code,
            wait_reason=reason,
            completed_at=None,
        )
        updated = replace(
            self._replace_step(run, step_run),
            status=WorkflowRunStatus.WAITING,
            current_step_id=step_id,
            resume_token=None,
            resume_token_hash=_resume_token_hash(token),
            wait_reason=reason,
        )
        persisted = self._persist_run(
            run,
            updated,
            "WORKFLOW_RUN_WAITING",
            {"step_id": step_id, "wait_reason": reason.value, "error_code": error_code},
        )
        return replace(persisted, resume_token=token)

    def _fail_step(
        self, run: WorkflowRun, step_id: str, error_code: str
    ) -> WorkflowRun:
        current = next(
            (item for item in run.step_runs if item.step_id == step_id), None
        )
        now = self._now()
        if current is None:
            failed = StepRun(
                step_id=step_id,
                status=StepRunStatus.FAILED,
                attempts=0,
                retry_count=0,
                input_json=None,
                output_json=None,
                error_code=error_code,
                wait_reason=None,
                started_at=now,
                completed_at=now,
            )
        else:
            failed = replace(
                current,
                status=StepRunStatus.FAILED,
                error_code=error_code,
                wait_reason=None,
                completed_at=now,
            )
        updated = replace(
            self._replace_step(run, failed),
            status=WorkflowRunStatus.FAILED,
            resume_token=None,
            resume_token_hash=None,
            wait_reason=None,
        )
        return self._persist_run(
            run,
            updated,
            "WORKFLOW_RUN_FAILED",
            {"step_id": step_id, "error_code": error_code},
        )

    def _complete_step(
        self,
        run: WorkflowRun,
        step_run: StepRun,
        next_step_id: str | None,
    ) -> WorkflowRun:
        status = (
            WorkflowRunStatus.SUCCEEDED
            if next_step_id is None
            else WorkflowRunStatus.RUNNING
        )
        updated = replace(
            self._replace_step(run, step_run),
            status=status,
            current_step_id=next_step_id,
            resume_token=None,
            resume_token_hash=None,
            wait_reason=None,
        )
        event_type = (
            "WORKFLOW_RUN_SUCCEEDED"
            if status is WorkflowRunStatus.SUCCEEDED
            else "WORKFLOW_STEP_STATE_CHANGED"
        )
        return self._persist_run(
            run,
            updated,
            event_type,
            {
                "step_id": step_run.step_id,
                "step_status": step_run.status.value,
                "next_step_id": next_step_id,
                "run_status": status.value,
            },
        )

    def _finish_run(
        self,
        run: WorkflowRun,
        status: WorkflowRunStatus,
        error_code: str | None,
    ) -> WorkflowRun:
        if run.status is status:
            return run
        updated = replace(
            run,
            status=status,
            resume_token=None,
            resume_token_hash=None,
            wait_reason=None,
        )
        return self._persist_run(
            run,
            updated,
            f"WORKFLOW_RUN_{status.value}",
            {"workflow_id": run.workflow_id, "error_code": error_code},
        )

    def _persist_run(
        self,
        current: WorkflowRun,
        updated: WorkflowRun,
        event_type: str,
        payload: dict[str, object],
    ) -> WorkflowRun:
        now = self._now()
        updated = replace(
            updated,
            revision=current.revision + 1,
            updated_at=now,
        )
        event = _event(updated, event_type, now, payload)
        if not self._repository.compare_and_set(
            run_id=current.run_id,
            expected_revision=current.revision,
            updated_run=updated,
            event=event,
        ):
            raise WorkflowStateConflict()
        return updated

    def _replace_step(self, run: WorkflowRun, step_run: StepRun) -> WorkflowRun:
        existing = {item.step_id: item for item in run.step_runs}
        existing[step_run.step_id] = step_run
        ordered = list(run.step_runs)
        for index, existing_step in enumerate(ordered):
            if existing_step.step_id == step_run.step_id:
                ordered[index] = step_run
                break
        else:
            ordered.append(step_run)
        return replace(run, step_runs=tuple(ordered))

    def _step_run(self, run: WorkflowRun, step_id: str) -> StepRun:
        for step_run in run.step_runs:
            if step_run.step_id == step_id:
                return step_run
        raise WorkflowStateConflict(f"StepRun {step_id} is missing")

    def _variables(self, run: WorkflowRun) -> dict[str, object]:
        steps: dict[str, object] = {}
        for step_run in run.step_runs:
            if step_run.output_json is not None:
                steps[step_run.step_id] = {"output": _decode_json(step_run.output_json)}
        return {
            "input": _decode_json(run.input_json),
            "context": _decode_json(run.run_context_json),
            "steps": steps,
        }

    def _next_step(
        self,
        step: Mapping[str, object],
        branch_target: str | None,
        positions: Mapping[str, int],
    ) -> str | None:
        if branch_target is not None:
            return None if branch_target == "END" else branch_target
        target = step.get("next")
        if isinstance(target, str):
            return None if target == "END" else target
        current = cast(str, step["id"])
        next_position = positions[current] + 1
        if next_position >= len(positions):
            return None
        return next(
            key for key, position in positions.items() if position == next_position
        )

    def _now(self) -> datetime:
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError("workflow clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


def _event(
    run: WorkflowRun,
    event_type: str,
    timestamp: datetime,
    payload: dict[str, object],
) -> EventRecord:
    return event_from_dict(
        {
            "event_id": str(uuid4()),
            "schema_version": 1,
            "timestamp": timestamp.isoformat(),
            "organization_id": run.organization_id,
            "trace_id": run.trace_id,
            "event_type": event_type,
            "payload": payload,
        }
    )


def _step_idempotency_key(run: WorkflowRun, step_id: str) -> str:
    raw = f"{run.organization_id}:{run.run_id}:{step_id}".encode()
    return "workflow:" + sha256(raw).hexdigest()


def _resume_token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def _canonical_json(value: object, label: str) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must contain JSON values") from error


def _decode_json(value: str | None) -> object:
    if value is None:
        return None
    return json.loads(value)


def _required_text(value: Mapping[str, object], field: str) -> str:
    text = value.get(field)
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"workflow run_context requires {field}")
    return text


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
