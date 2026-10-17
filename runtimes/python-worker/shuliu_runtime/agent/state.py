from __future__ import annotations

from enum import Enum


class TaskState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AgentRunState(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class InvalidAgentTransition(ValueError):
    """Raised when a Task or AgentRun transition is not allowed."""


_TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.QUEUED: frozenset({TaskState.RUNNING, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset(
        {
            TaskState.WAITING,
            TaskState.SUCCEEDED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.WAITING: frozenset(
        {TaskState.RUNNING, TaskState.FAILED, TaskState.CANCELLED}
    ),
}

_AGENT_RUN_TRANSITIONS: dict[AgentRunState, frozenset[AgentRunState]] = {
    AgentRunState.CREATED: frozenset({AgentRunState.RUNNING, AgentRunState.CANCELLED}),
    AgentRunState.RUNNING: frozenset(
        {
            AgentRunState.WAITING,
            AgentRunState.SUCCEEDED,
            AgentRunState.FAILED,
            AgentRunState.CANCELLED,
        }
    ),
    AgentRunState.WAITING: frozenset(
        {AgentRunState.RUNNING, AgentRunState.FAILED, AgentRunState.CANCELLED}
    ),
}


def next_task_state(current: TaskState, target: TaskState) -> TaskState:
    if not isinstance(current, TaskState) or not isinstance(target, TaskState):
        raise InvalidAgentTransition("Task states must use TaskState values")
    if target not in _TASK_TRANSITIONS.get(current, frozenset()):
        raise InvalidAgentTransition(
            f"Task cannot transition from {current.value} to {target.value}"
        )
    return target


def next_agent_run_state(
    current: AgentRunState, target: AgentRunState
) -> AgentRunState:
    if not isinstance(current, AgentRunState) or not isinstance(target, AgentRunState):
        raise InvalidAgentTransition("AgentRun states must use AgentRunState values")
    if target not in _AGENT_RUN_TRANSITIONS.get(current, frozenset()):
        raise InvalidAgentTransition(
            f"AgentRun cannot transition from {current.value} to {target.value}"
        )
    return target


def task_state_for_run(state: AgentRunState) -> TaskState:
    if not isinstance(state, AgentRunState):
        raise InvalidAgentTransition("AgentRun state is invalid")
    return (
        TaskState[state.value]
        if state is not AgentRunState.CREATED
        else TaskState.QUEUED
    )
