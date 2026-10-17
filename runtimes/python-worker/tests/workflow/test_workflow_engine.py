from __future__ import annotations

from threading import RLock

import pytest
from shuliu_runtime.events.models import EventRecord
from shuliu_runtime.policy.rules import PolicyOutcome
from shuliu_runtime.tools.broker import (
    ToolExecutionError,
    ToolExecutionStatus,
    ToolResult,
    VerificationStatus,
)
from shuliu_runtime.workflow.definition import (
    WorkflowDefinition,
    WorkflowDefinitionError,
)
from shuliu_runtime.workflow.engine import (
    StepRunStatus,
    WorkflowEngine,
    WorkflowRunStatus,
    WorkflowStepError,
)


class InMemoryWorkflowRepository:
    def __init__(self) -> None:
        self.runs = {}
        self.events: list[EventRecord] = []
        self.lock = RLock()

    def create(self, run, event) -> bool:
        with self.lock:
            if run.run_id in self.runs:
                return False
            self.runs[run.run_id] = run
            self.events.append(event)
            return True

    def get(self, run_id):
        with self.lock:
            return self.runs.get(run_id)

    def compare_and_set(self, *, run_id, expected_revision, updated_run, event):
        with self.lock:
            current = self.runs.get(run_id)
            if current is None or current.revision != expected_revision:
                return False
            self.runs[run_id] = updated_run
            self.events.append(event)
            return True


class FakeToolBroker:
    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = []

    def execute(self, tool_id, args, actor, context, *, idempotency_key):
        self.calls.append(
            {
                "tool_id": tool_id,
                "args": dict(args),
                "actor": dict(actor),
                "context": dict(context),
                "idempotency_key": idempotency_key,
            }
        )
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return _tool_result()


def _tool_result(output=None, verification=VerificationStatus.VERIFIED):
    return ToolResult(
        tool_id="foundation.test",
        tool_version="1.0.0",
        execution_status=ToolExecutionStatus.SUCCEEDED,
        verification_status=verification,
        output={"ok": True} if output is None else output,
        error_code=None
        if verification is VerificationStatus.VERIFIED
        else "UNVERIFIED",
        policy_decision=PolicyOutcome.ALLOW,
        policy_version="policy-v1",
        policy_rule_id="allow-test",
    )


def _definition(steps, version="workflow-v1", input_schema=None):
    return {
        "workflow_id": "generic-workflow",
        "workflow_version_id": version,
        "input_schema": input_schema or {"type": "object"},
        "steps": steps,
    }


def _tool_step(step_id="call", tool_id="foundation.test", **extra):
    return {
        "id": step_id,
        "type": "tool",
        "tool_id": tool_id,
        "arguments": {"value": {"$ref": "input.value"}},
        **extra,
    }


def _context(**extra):
    return {
        "organization_id": "org-1",
        "trace_id": "trace-1",
        "actor": {
            "subject_id": "user-1",
            "organization_id": "org-1",
            "permissions": ["foundation.test"],
        },
        **extra,
    }


def _engine(*outcomes):
    repository = InMemoryWorkflowRepository()
    broker = FakeToolBroker(*outcomes)
    return WorkflowEngine(repository=repository, tool_broker=broker), repository, broker


def test_unknown_step_type_is_rejected_by_workflow_schema_validation():
    with pytest.raises(WorkflowDefinitionError, match="step"):
        WorkflowDefinition.from_dict(_definition([{"id": "unknown", "type": "shell"}]))


def test_workflow_input_is_checked_against_the_versioned_input_schema():
    engine, repository, broker = _engine()
    definition = WorkflowDefinition.from_dict(
        _definition(
            [_tool_step()],
            input_schema={
                "type": "object",
                "required": ["value"],
                "properties": {"value": {"type": "string"}},
                "additionalProperties": False,
            },
        )
    )

    with pytest.raises(ValueError, match="input"):
        engine.start_workflow(definition, {"value": 123}, _context())

    assert repository.runs == {}
    assert broker.calls == []


def test_definition_rejects_backward_branches_and_future_output_references():
    backward_branch = _definition(
        [
            _tool_step("first", next="END"),
            {
                "id": "branch",
                "type": "condition",
                "condition": {"path": "input.ok", "operator": "truthy"},
                "then": "first",
                "else": "END",
            },
        ]
    )
    with pytest.raises(WorkflowDefinitionError, match="later"):
        WorkflowDefinition.from_dict(backward_branch)

    future_reference = _definition(
        [
            {
                "id": "first",
                "type": "tool",
                "tool_id": "foundation.first",
                "arguments": {"value": {"$ref": "steps.second.output.value"}},
            },
            _tool_step("second", next="END"),
        ]
    )
    with pytest.raises(WorkflowDefinitionError, match="earlier"):
        WorkflowDefinition.from_dict(future_reference)


def test_retry_stops_at_explicit_maximum_and_does_not_retry_indefinitely():
    engine, _, broker = _engine(
        WorkflowStepError("RATE_LIMITED", retryable=True),
        WorkflowStepError("RATE_LIMITED", retryable=True),
        _tool_result(),
    )
    definition = WorkflowDefinition.from_dict(
        _definition(
            [
                _tool_step(
                    retry={"max_retries": 1},
                    on_failure={"action": "fail"},
                )
            ]
        )
    )

    run = engine.start_workflow(definition, {"value": "x"}, _context())

    assert run.status is WorkflowRunStatus.FAILED
    assert len(broker.calls) == 2
    assert run.step_runs[0].retry_count == 1
    assert run.step_runs[0].error_code == "RATE_LIMITED"


def test_condition_branches_only_to_declared_forward_steps():
    engine, _, broker = _engine(_tool_result({"branch": "yes"}))
    definition = WorkflowDefinition.from_dict(
        _definition(
            [
                {
                    "id": "choose",
                    "type": "condition",
                    "condition": {
                        "path": "input.enabled",
                        "operator": "eq",
                        "value": True,
                    },
                    "then": "yes",
                    "else": "no",
                },
                _tool_step("yes", "foundation.yes", next="END"),
                _tool_step("no", "foundation.no", next="END"),
            ]
        )
    )

    run = engine.start_workflow(definition, {"enabled": True, "value": "x"}, _context())

    assert run.status is WorkflowRunStatus.SUCCEEDED
    assert [call["tool_id"] for call in broker.calls] == ["foundation.yes"]
    assert run.step_runs[0].status is StepRunStatus.SUCCEEDED


def test_tool_args_resolve_input_and_prior_step_output_through_broker():
    engine, _, broker = _engine(
        _tool_result({"answer": 42}),
        _tool_result({"accepted": True}),
    )
    definition = WorkflowDefinition.from_dict(
        _definition(
            [
                {
                    "id": "first",
                    "type": "tool",
                    "tool_id": "foundation.first",
                    "arguments": {"value": {"$ref": "input.value"}},
                },
                {
                    "id": "second",
                    "type": "tool",
                    "tool_id": "foundation.second",
                    "arguments": {"answer": {"$ref": "steps.first.output.answer"}},
                },
            ]
        )
    )

    run = engine.start_workflow(definition, {"value": "source"}, _context())

    assert run.status is WorkflowRunStatus.SUCCEEDED
    assert broker.calls[0]["args"] == {"value": "source"}
    assert broker.calls[1]["args"] == {"answer": 42}
    assert all(call["idempotency_key"] for call in broker.calls)
    assert len({call["idempotency_key"] for call in broker.calls}) == 2
    assert all(call["context"]["trace_id"] == "trace-1" for call in broker.calls)


def test_policy_denial_cannot_retry_or_follow_a_fallback_tool():
    engine, _, broker = _engine(
        ToolExecutionError("POLICY_DENIED", "denied"),
        _tool_result(),
    )
    definition = WorkflowDefinition.from_dict(
        _definition(
            [
                _tool_step(
                    "primary",
                    "foundation.primary",
                    retry={"max_retries": 3},
                    next="END",
                    on_failure={"action": "fallback", "step_id": "fallback"},
                ),
                _tool_step("fallback", "foundation.fallback", next="END"),
            ]
        )
    )

    run = engine.start_workflow(definition, {"value": "x"}, _context())

    assert run.status is WorkflowRunStatus.FAILED
    assert run.step_runs[0].error_code == "POLICY_DENIED"
    assert [call["tool_id"] for call in broker.calls] == ["foundation.primary"]


def test_fallback_runs_only_for_a_step_error_marked_fallback_safe():
    engine, _, broker = _engine(
        WorkflowStepError("OPTIONAL_STEP_UNAVAILABLE", fallback_safe=True),
        _tool_result({"fallback": True}),
    )
    definition = WorkflowDefinition.from_dict(
        _definition(
            [
                _tool_step(
                    "primary",
                    "foundation.primary",
                    next="END",
                    on_failure={"action": "fallback", "step_id": "fallback"},
                ),
                _tool_step("fallback", "foundation.fallback", next="END"),
            ]
        )
    )

    run = engine.start_workflow(definition, {"value": "x"}, _context())

    assert run.status is WorkflowRunStatus.SUCCEEDED
    assert run.step_runs[0].status is StepRunStatus.FAILED
    assert [call["tool_id"] for call in broker.calls] == [
        "foundation.primary",
        "foundation.fallback",
    ]


def test_unverified_tool_result_fails_without_running_following_steps():
    engine, _, broker = _engine(
        _tool_result({"done": False}, VerificationStatus.FAILED),
        _tool_result(),
    )
    definition = WorkflowDefinition.from_dict(
        _definition(
            [
                _tool_step("first", "foundation.first"),
                _tool_step("second", "foundation.second", next="END"),
            ]
        )
    )

    run = engine.start_workflow(definition, {"value": "x"}, _context())

    assert run.status is WorkflowRunStatus.FAILED
    assert run.step_runs[0].error_code == "UNVERIFIED"
    assert [call["tool_id"] for call in broker.calls] == ["foundation.first"]
