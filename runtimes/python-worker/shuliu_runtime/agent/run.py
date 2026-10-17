from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from shuliu_runtime.events.models import EventRecord, event_from_dict, event_to_dict

from .snapshot import VersionSnapshot
from .state import (
    AgentRunState,
    InvalidAgentTransition,
    TaskState,
    next_agent_run_state,
    next_task_state,
    task_state_for_run,
)


class AgentCoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AgentStateConflict(AgentCoreError):
    def __init__(self, message: str = "Agent state changed concurrently") -> None:
        super().__init__("AGENT_STATE_CONFLICT", message)


class AgentTaskNotFound(AgentCoreError):
    def __init__(self, task_id: str) -> None:
        super().__init__("TASK_NOT_FOUND", f"Task {task_id} was not found")


class AgentRunNotFound(AgentCoreError):
    def __init__(self, run_id: str) -> None:
        super().__init__("AGENT_RUN_NOT_FOUND", f"AgentRun {run_id} was not found")


@dataclass(frozen=True)
class Task:
    task_id: str
    organization_id: str
    trace_id: str
    state: TaskState
    created_at: datetime
    updated_at: datetime
    revision: int = 0


@dataclass(frozen=True)
class AgentRun:
    run_id: str
    task_id: str
    organization_id: str
    trace_id: str
    state: AgentRunState
    snapshot: VersionSnapshot
    created_at: datetime
    updated_at: datetime
    revision: int = 0


class AgentRepository(Protocol):
    """Persistence port; state and Event writes must commit atomically."""

    def create_task(self, task: Task, event: EventRecord) -> bool: ...

    def get_task(self, task_id: str) -> Task | None: ...

    def create_run(self, run: AgentRun, event: EventRecord) -> bool: ...

    def get_run(self, run_id: str) -> AgentRun | None: ...

    def compare_and_set_run(
        self,
        *,
        run_id: str,
        expected_run_state: AgentRunState,
        expected_run_revision: int,
        expected_task_state: TaskState,
        expected_task_revision: int,
        updated_run: AgentRun,
        updated_task: Task,
        event: EventRecord,
    ) -> bool: ...


Clock = Callable[[], datetime]


class AgentCore:
    """Business-neutral Task and AgentRun state service."""

    def __init__(self, *, repository: AgentRepository, clock: Clock | None = None):
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    def create_task(self, *, task_id: str, organization_id: str, trace_id: str) -> Task:
        if not _non_empty(task_id):
            raise ValueError("task_id is required")
        if not _non_empty(organization_id):
            raise ValueError("organization_id is required")
        if not _non_empty(trace_id):
            raise ValueError("trace_id is required")
        now = self._now()
        task = Task(
            task_id=task_id,
            organization_id=organization_id,
            trace_id=trace_id,
            state=TaskState.QUEUED,
            created_at=now,
            updated_at=now,
        )
        event = _state_event(
            event_id=str(uuid4()),
            event_type="TASK_CREATED",
            organization_id=organization_id,
            trace_id=trace_id,
            timestamp=now,
            payload={"task_id": task_id, "state": task.state.value},
        )
        if not self._repository.create_task(task, event):
            raise AgentStateConflict("Task ID already exists")
        return task

    def create_run(
        self,
        task: Task,
        agent_version: str,
        resolved_versions: Mapping[str, object],
    ) -> AgentRun:
        if not isinstance(task, Task):
            raise TypeError("task must be a Task record")
        snapshot = VersionSnapshot.create(agent_version, resolved_versions)
        stored_task = self._repository.get_task(task.task_id)
        if stored_task is None:
            raise AgentTaskNotFound(task.task_id)
        if (
            stored_task.organization_id != task.organization_id
            or stored_task.trace_id != task.trace_id
            or stored_task.state is not TaskState.QUEUED
        ):
            raise AgentStateConflict("Task is not queued for a new AgentRun")
        now = self._now()
        run = AgentRun(
            run_id=str(uuid4()),
            task_id=stored_task.task_id,
            organization_id=stored_task.organization_id,
            trace_id=stored_task.trace_id,
            state=AgentRunState.CREATED,
            snapshot=snapshot,
            created_at=now,
            updated_at=now,
        )
        event = _state_event(
            event_id=str(uuid4()),
            event_type="AGENT_RUN_CREATED",
            organization_id=run.organization_id,
            trace_id=run.trace_id,
            timestamp=now,
            payload={
                "run_id": run.run_id,
                "task_id": run.task_id,
                "agent_version": run.snapshot.agent_version,
                "state": run.state.value,
            },
        )
        if not self._repository.create_run(run, event):
            raise AgentStateConflict("AgentRun ID already exists")
        return run

    def transition(
        self,
        run_id: str,
        expected_state: AgentRunState,
        next_state: AgentRunState,
        event: EventRecord,
    ) -> AgentRun:
        if not _non_empty(run_id):
            raise ValueError("run_id is required")
        if not isinstance(event, EventRecord):
            raise TypeError("state transition event must be an EventRecord")
        if not isinstance(expected_state, AgentRunState) or not isinstance(
            next_state, AgentRunState
        ):
            raise InvalidAgentTransition(
                "AgentRun states must use AgentRunState values"
            )
        run = self._repository.get_run(run_id)
        if run is None:
            raise AgentRunNotFound(run_id)
        if run.state is not expected_state:
            raise AgentStateConflict(
                f"Expected {expected_state.value}, found {run.state.value}"
            )
        next_agent_run_state(run.state, next_state)
        task = self._repository.get_task(run.task_id)
        if task is None:
            raise AgentTaskNotFound(run.task_id)
        expected_task_state = task_state_for_run(run.state)
        if (
            task.organization_id != run.organization_id
            or task.trace_id != run.trace_id
            or task.state is not expected_task_state
        ):
            raise AgentStateConflict("Task and AgentRun state are inconsistent")
        _validate_transition_event(event, run, next_state)
        expected_revision = event.payload.get("expected_revision")
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision != run.revision
        ):
            raise AgentStateConflict("AgentRun revision changed before transition")
        next_status = task_state_for_run(next_state)
        next_task_state(task.state, next_status)

        now = self._now()
        updated_run = replace(
            run,
            state=next_state,
            updated_at=now,
            revision=run.revision + 1,
        )
        updated_task = replace(
            task,
            state=next_status,
            updated_at=now,
            revision=task.revision + 1,
        )
        if not self._repository.compare_and_set_run(
            run_id=run.run_id,
            expected_run_state=run.state,
            expected_run_revision=run.revision,
            expected_task_state=task.state,
            expected_task_revision=task.revision,
            updated_run=updated_run,
            updated_task=updated_task,
            event=event,
        ):
            raise AgentStateConflict()
        return updated_run

    def _now(self) -> datetime:
        now = self._clock()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ValueError("clock must return a timezone-aware datetime")
        return now.astimezone(UTC)


def _validate_transition_event(
    event: EventRecord, run: AgentRun, next_state: AgentRunState
) -> None:
    if not isinstance(event, EventRecord):
        raise TypeError("state transition event must be an EventRecord")
    try:
        validated = event_from_dict(event_to_dict(event))
    except (TypeError, ValueError) as error:
        raise ValueError("state transition event is invalid") from error
    payload = validated.payload
    if (
        validated.event_type != "AGENT_RUN_STATE_CHANGED"
        or validated.organization_id != run.organization_id
        or validated.trace_id != run.trace_id
        or payload.get("run_id") != run.run_id
        or payload.get("task_id") != run.task_id
        or payload.get("from_state") != run.state.value
        or payload.get("to_state") != next_state.value
    ):
        raise ValueError("state transition event does not match run scope or trace")


def _state_event(
    *,
    event_id: str,
    event_type: str,
    organization_id: str,
    trace_id: str,
    timestamp: datetime,
    payload: dict[str, object],
) -> EventRecord:
    return event_from_dict(
        {
            "event_id": event_id,
            "schema_version": 1,
            "timestamp": timestamp.isoformat(),
            "organization_id": organization_id,
            "trace_id": trace_id,
            "event_type": event_type,
            "payload": payload,
        }
    )


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
