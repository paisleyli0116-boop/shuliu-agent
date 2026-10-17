from __future__ import annotations

import pytest
from shuliu_runtime.tools.broker import ToolExecutionError
from shuliu_runtime.workflow.definition import WorkflowDefinition
from shuliu_runtime.workflow.engine import (
    WorkflowResumeRejected,
    WorkflowRunStatus,
    WorkflowStepError,
    WorkflowWaitReason,
)

from tests.workflow.test_workflow_engine import (
    _context,
    _definition,
    _engine,
    _tool_result,
    _tool_step,
)


def test_pending_approval_is_saved_and_resumed_through_tool_broker():
    engine, repository, broker = _engine(
        ToolExecutionError("APPROVAL_NOT_CONSUMABLE", "approval is pending"),
        _tool_result({"approved_action": True}),
    )
    definition = WorkflowDefinition.from_dict(
        _definition([_tool_step()], version="workflow-v1")
    )

    waiting = engine.start_workflow(
        definition,
        {"value": "x"},
        _context(approval_id="approval-1"),
    )

    assert waiting.status is WorkflowRunStatus.WAITING
    assert waiting.wait_reason is WorkflowWaitReason.APPROVAL
    assert waiting.resume_token
    persisted = repository.get(waiting.run_id)
    assert persisted.status is WorkflowRunStatus.WAITING
    assert persisted.resume_token is None
    assert persisted.resume_token_hash
    assert len(broker.calls) == 1
    with pytest.raises(WorkflowResumeRejected):
        engine.resume_workflow(waiting.run_id, "wrong-token")
    assert len(broker.calls) == 1

    resumed = engine.resume_workflow(waiting.run_id, waiting.resume_token)

    assert resumed.status is WorkflowRunStatus.SUCCEEDED
    assert resumed.resume_token is None
    assert len(broker.calls) == 2
    assert broker.calls[0]["idempotency_key"] == broker.calls[1]["idempotency_key"]
    with pytest.raises(WorkflowResumeRejected):
        engine.resume_workflow(waiting.run_id, waiting.resume_token)


def test_explicit_external_pause_can_resume_with_its_one_time_token():
    engine, _, broker = _engine(
        WorkflowStepError("DEPENDENCY_WAIT", resumable=True),
        _tool_result({"resumed": True}),
    )
    definition = WorkflowDefinition.from_dict(
        _definition(
            [
                _tool_step(
                    on_failure={"action": "pause"},
                )
            ]
        )
    )

    waiting = engine.start_workflow(definition, {"value": "x"}, _context())

    assert waiting.status is WorkflowRunStatus.WAITING
    assert waiting.wait_reason is WorkflowWaitReason.EXTERNAL
    assert waiting.resume_token

    resumed = engine.resume_workflow(waiting.run_id, waiting.resume_token)

    assert resumed.status is WorkflowRunStatus.SUCCEEDED
    assert broker.calls[0]["idempotency_key"] == broker.calls[1]["idempotency_key"]


def test_running_workflow_keeps_its_definition_snapshot_after_new_version_exists():
    engine, _, broker = _engine(
        ToolExecutionError("APPROVAL_NOT_CONSUMABLE", "approval is pending"),
        _tool_result(),
    )
    source = _definition([_tool_step(tool_id="foundation.v1")], version="v1")
    definition_v1 = WorkflowDefinition.from_dict(source)

    waiting = engine.start_workflow(
        definition_v1,
        {"value": "x"},
        _context(approval_id="approval-2"),
    )
    source["workflow_version_id"] = "v2"
    source["steps"][0]["tool_id"] = "foundation.v2"
    WorkflowDefinition.from_dict(source)

    resumed = engine.resume_workflow(waiting.run_id, waiting.resume_token)

    assert resumed.workflow_version_id == "v1"
    assert resumed.status is WorkflowRunStatus.SUCCEEDED
    assert [call["tool_id"] for call in broker.calls] == [
        "foundation.v1",
        "foundation.v1",
    ]
