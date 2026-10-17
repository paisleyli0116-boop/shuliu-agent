from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, RLock
from uuid import uuid4

import pytest
from shuliu_runtime.agent.run import AgentCore, AgentStateConflict
from shuliu_runtime.agent.state import AgentRunState, InvalidAgentTransition, TaskState
from shuliu_runtime.events.models import EventRecord


class InMemoryAgentRepository:
    """Atomic repository test double for the Agent Core repository port."""

    def __init__(self) -> None:
        self.tasks = {}
        self.runs = {}
        self.events = []
        self.lock = RLock()
        self.read_barrier: Barrier | None = None

    def create_task(self, task, event) -> bool:
        with self.lock:
            if task.task_id in self.tasks:
                return False
            self.tasks[task.task_id] = task
            self.events.append(event)
            return True

    def get_task(self, task_id):
        with self.lock:
            return self.tasks.get(task_id)

    def create_run(self, run, event) -> bool:
        with self.lock:
            if run.run_id in self.runs:
                return False
            self.runs[run.run_id] = run
            self.events.append(event)
            return True

    def get_run(self, run_id):
        with self.lock:
            run = self.runs.get(run_id)
        barrier = self.read_barrier
        if barrier is not None:
            barrier.wait(timeout=2)
        return run

    def compare_and_set_run(
        self,
        *,
        run_id,
        expected_run_state,
        expected_run_revision,
        expected_task_state,
        expected_task_revision,
        updated_run,
        updated_task,
        event,
    ) -> bool:
        with self.lock:
            current_run = self.runs.get(run_id)
            current_task = self.tasks.get(updated_task.task_id)
            if (
                current_run is None
                or current_task is None
                or current_run.state is not expected_run_state
                or current_run.revision != expected_run_revision
                or current_task.state is not expected_task_state
                or current_task.revision != expected_task_revision
            ):
                return False
            self.runs[run_id] = updated_run
            self.tasks[updated_task.task_id] = updated_task
            self.events.append(event)
            return True


def _clock() -> datetime:
    return datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def _create_core():
    repository = InMemoryAgentRepository()
    core = AgentCore(repository=repository, clock=_clock)
    task = core.create_task(
        task_id="task-1", organization_id="org-1", trace_id="trace-1"
    )
    run = core.create_run(
        task,
        agent_version="agent-1.0.0",
        resolved_versions={
            "skill:generic": "1.0.0",
            "workflow:generic": "1.0.0",
            "policy": "policy-v1",
            "runtime": "runtime-v0.1",
            "protocol.agent": "1",
        },
    )
    return core, repository, task, run


def _event(run, from_state: AgentRunState, to_state: AgentRunState) -> EventRecord:
    return EventRecord(
        event_id=str(uuid4()),
        schema_version=1,
        timestamp=_clock().isoformat(),
        organization_id=run.organization_id,
        trace_id=run.trace_id,
        event_type="AGENT_RUN_STATE_CHANGED",
        payload={
            "run_id": run.run_id,
            "task_id": run.task_id,
            "from_state": from_state.value,
            "to_state": to_state.value,
            "expected_revision": run.revision,
        },
    )


def test_state_changes_update_task_and_run_with_trace_bound_event():
    core, repository, task, run = _create_core()

    started = core.transition(
        run.run_id,
        expected_state=AgentRunState.CREATED,
        next_state=AgentRunState.RUNNING,
        event=_event(run, AgentRunState.CREATED, AgentRunState.RUNNING),
    )

    assert started.state is AgentRunState.RUNNING
    assert repository.tasks[task.task_id].state is TaskState.RUNNING
    assert started.trace_id == task.trace_id == "trace-1"
    assert repository.events[-1].trace_id == started.trace_id
    assert repository.events[-1].payload["to_state"] == "RUNNING"


def test_illegal_transition_is_rejected_without_mutating_state_or_event_log():
    core, repository, task, run = _create_core()
    events_before = tuple(repository.events)

    with pytest.raises(InvalidAgentTransition):
        core.transition(
            run.run_id,
            expected_state=AgentRunState.CREATED,
            next_state=AgentRunState.SUCCEEDED,
            event=_event(run, AgentRunState.CREATED, AgentRunState.SUCCEEDED),
        )

    assert repository.runs[run.run_id] == run
    assert repository.tasks[task.task_id].state is TaskState.QUEUED
    assert tuple(repository.events) == events_before


def test_concurrent_duplicate_completion_commits_only_once():
    core, repository, _, run = _create_core()
    running = core.transition(
        run.run_id,
        expected_state=AgentRunState.CREATED,
        next_state=AgentRunState.RUNNING,
        event=_event(run, AgentRunState.CREATED, AgentRunState.RUNNING),
    )
    repository.read_barrier = Barrier(2)

    def complete():
        try:
            return core.transition(
                running.run_id,
                expected_state=AgentRunState.RUNNING,
                next_state=AgentRunState.SUCCEEDED,
                event=_event(running, AgentRunState.RUNNING, AgentRunState.SUCCEEDED),
            )
        except AgentStateConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: complete(), range(2)))

    assert sum(not isinstance(value, AgentStateConflict) for value in outcomes) == 1
    assert sum(isinstance(value, AgentStateConflict) for value in outcomes) == 1
    assert repository.runs[run.run_id].state is AgentRunState.SUCCEEDED
    assert repository.tasks[run.task_id].state is TaskState.SUCCEEDED
    assert (
        sum(
            event.event_type == "AGENT_RUN_STATE_CHANGED" for event in repository.events
        )
        == 2
    )


def test_event_with_lost_or_mismatched_trace_is_rejected_before_write():
    core, repository, _, run = _create_core()
    event = EventRecord(
        event_id="event-wrong-trace",
        schema_version=1,
        timestamp=_clock().isoformat(),
        organization_id=run.organization_id,
        trace_id="trace-other",
        event_type="AGENT_RUN_STATE_CHANGED",
        payload={
            "run_id": run.run_id,
            "task_id": run.task_id,
            "from_state": "CREATED",
            "to_state": "RUNNING",
        },
    )
    events_before = tuple(repository.events)

    with pytest.raises(ValueError, match="trace"):
        core.transition(
            run.run_id,
            expected_state=AgentRunState.CREATED,
            next_state=AgentRunState.RUNNING,
            event=event,
        )

    assert repository.runs[run.run_id] == run
    assert tuple(repository.events) == events_before


def test_task_creation_requires_trace_id():
    core = AgentCore(repository=InMemoryAgentRepository(), clock=_clock)

    with pytest.raises(ValueError, match="trace_id"):
        core.create_task(task_id="task-1", organization_id="org-1", trace_id=" ")


def test_transition_rejects_state_values_outside_the_agent_run_enum():
    core, _, _, run = _create_core()

    with pytest.raises(InvalidAgentTransition):
        core.transition(
            run.run_id,
            expected_state="CREATED",
            next_state=AgentRunState.RUNNING,
            event=_event(run, AgentRunState.CREATED, AgentRunState.RUNNING),
        )


def test_transition_rejects_non_event_values_before_repository_write():
    core, repository, _, run = _create_core()
    events_before = tuple(repository.events)

    with pytest.raises(TypeError, match="event"):
        core.transition(
            run.run_id,
            expected_state=AgentRunState.CREATED,
            next_state=AgentRunState.RUNNING,
            event=None,
        )

    assert repository.runs[run.run_id] == run
    assert tuple(repository.events) == events_before


def test_stale_transition_is_rejected_after_run_returns_to_the_same_state():
    core, repository, _, run = _create_core()
    running = core.transition(
        run.run_id,
        expected_state=AgentRunState.CREATED,
        next_state=AgentRunState.RUNNING,
        event=_event(run, AgentRunState.CREATED, AgentRunState.RUNNING),
    )
    waiting = core.transition(
        run.run_id,
        expected_state=AgentRunState.RUNNING,
        next_state=AgentRunState.WAITING,
        event=_event(running, AgentRunState.RUNNING, AgentRunState.WAITING),
    )
    resumed = core.transition(
        run.run_id,
        expected_state=AgentRunState.WAITING,
        next_state=AgentRunState.RUNNING,
        event=_event(waiting, AgentRunState.WAITING, AgentRunState.RUNNING),
    )
    events_before = tuple(repository.events)

    with pytest.raises(AgentStateConflict):
        core.transition(
            running.run_id,
            expected_state=AgentRunState.RUNNING,
            next_state=AgentRunState.SUCCEEDED,
            event=_event(running, AgentRunState.RUNNING, AgentRunState.SUCCEEDED),
        )

    assert repository.runs[run.run_id] == resumed
    assert repository.tasks[run.task_id].state is TaskState.RUNNING
    assert tuple(repository.events) == events_before
