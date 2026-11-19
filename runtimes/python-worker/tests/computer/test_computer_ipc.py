from __future__ import annotations

from threading import Event
from uuid import uuid4

import pytest

from shuliu_runtime.computer.ipc import create_computer_ipc_handlers
from shuliu_runtime.policy.rules import PolicyOutcome
from shuliu_runtime.tools.broker import (
    ToolExecutionStatus,
    ToolResult,
    VerificationStatus,
)


class RecordingComputerRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, object, object]] = []

    def capability(self, *, actor, context):
        self.calls.append(("capability", None, actor, context))
        return _result({"status": "AVAILABLE"})

    def observe(self, window_ref, *, actor, context):
        self.calls.append(("observe", window_ref, actor, context))
        return _result({"status": "OBSERVED", "window_ref": window_ref})

    def observe_foreground(self, *, actor, context):
        self.calls.append(("observe_foreground", None, actor, context))
        return _result({"status": "OBSERVED", "window_ref": "0x1234"})


def _result(output):
    return ToolResult(
        tool_id="computer.observe",
        tool_version="1.0.0",
        execution_status=ToolExecutionStatus.SUCCEEDED,
        verification_status=VerificationStatus.VERIFIED,
        output=output,
        error_code=None,
        policy_decision=PolicyOutcome.ALLOW,
        policy_version="computer-v1",
        policy_rule_id="computer-read",
    )


def _handlers(runtime):
    return create_computer_ipc_handlers(
        runtime,
        actor={"subject_id": "local-agent"},
        organization_id="local-workspace",
    )


def test_computer_capability_and_observe_use_sidecar_owned_identity_and_context():
    runtime = RecordingComputerRuntime()
    handlers = _handlers(runtime)
    trace_id = str(uuid4())

    capability = handlers["computer.capability"]({"trace_id": trace_id}, Event())
    observed = handlers["computer.observe"](
        {"trace_id": trace_id, "window_ref": "0x1234"}, Event()
    )

    assert capability["output"] == {"status": "AVAILABLE"}
    assert observed["output"] == {"status": "OBSERVED", "window_ref": "0x1234"}
    assert [call[0] for call in runtime.calls] == ["capability", "observe"]
    for _, _, actor, context in runtime.calls:
        assert actor == {"subject_id": "local-agent", "organization_id": "local-workspace"}
        assert context["organization_id"] == "local-workspace"
        assert context["trace_id"] == trace_id


def test_foreground_observation_does_not_accept_a_renderer_selected_window():
    runtime = RecordingComputerRuntime()
    handlers = _handlers(runtime)
    trace_id = str(uuid4())

    observed = handlers["computer.observe_foreground"]({"trace_id": trace_id}, Event())

    assert observed["status"] == "succeeded"
    assert observed["output"] == {"status": "OBSERVED", "window_ref": "0x1234"}
    assert runtime.calls == [(
        "observe_foreground",
        None,
        {"subject_id": "local-agent", "organization_id": "local-workspace"},
        {"organization_id": "local-workspace", "trace_id": trace_id, "privacy": "INTERNAL"},
    )]


def test_foreground_observation_rejects_target_spoofing_before_runtime():
    runtime = RecordingComputerRuntime()
    handlers = _handlers(runtime)

    with pytest.raises(ValueError):
        handlers["computer.observe_foreground"](
            {"trace_id": str(uuid4()), "window_ref": "0x9999"}, Event()
        )

    assert runtime.calls == []


@pytest.mark.parametrize(
    "params",
    [
        {"trace_id": str(uuid4()), "organization_id": "attacker-org"},
        {"trace_id": str(uuid4()), "actor": {"subject_id": "attacker"}},
        {"trace_id": "not-a-trace", "window_ref": "foreground"},
        {"trace_id": str(uuid4()), "window_ref": "C:\\Users\\someone\\private"},
    ],
)
def test_invalid_or_spoofed_computer_observe_request_is_rejected_before_runtime(params):
    runtime = RecordingComputerRuntime()
    handlers = _handlers(runtime)

    with pytest.raises(ValueError):
        handlers["computer.observe"](params, Event())

    assert runtime.calls == []


def test_cancelled_computer_observe_never_reads_the_desktop():
    runtime = RecordingComputerRuntime()
    handlers = _handlers(runtime)
    cancelled = Event()
    cancelled.set()

    result = handlers["computer.observe"](
        {"trace_id": str(uuid4()), "window_ref": "0x1234"}, cancelled
    )

    assert result["status"] == "cancelled"
    assert result["safe_error_code"] == "IPC_REQUEST_CANCELLED"
    assert runtime.calls == []
