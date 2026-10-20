from __future__ import annotations

import json
from copy import deepcopy
from collections.abc import Mapping
from datetime import timedelta

import pytest

from shuliu_runtime.approval.service import (
    Approval,
    ApprovalError,
    ApprovalService,
    ApprovalStatus,
)
from shuliu_runtime.computer.adapter import ComputerRuntime
from shuliu_runtime.computer.ufo_operator import UfoOperator, UfoOperatorError
from shuliu_runtime.events.models import EventRecord
from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.policy.rules import PolicyOutcome, PolicyRule
from shuliu_runtime.tools.broker import (
    IdentityPrincipal,
    ToolExecutionError,
    VerificationStatus,
)
from shuliu_runtime.tools.idempotency import InMemoryIdempotencyStore
from shuliu_runtime.computer.win32_backend import Win32Backend


class RecordingIdentity:
    def resolve(self, actor: Mapping[str, object]) -> IdentityPrincipal | None:
        if actor.get("subject_id") != "agent-1":
            return None
        return IdentityPrincipal("agent-1", "org-1", ("computer.read", "computer.act"))


class RecordingApproval:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def consume(self, approval_id: str, action_context: Mapping[str, object]) -> None:
        if approval_id != "approved-1":
            raise ApprovalError("APPROVAL_NOT_FOUND", "approval is unavailable")
        self.calls.append((approval_id, dict(action_context)))


class MemoryApprovalRepository:
    def __init__(self) -> None:
        self.records: dict[str, Approval] = {}

    def create(self, approval: Approval, event) -> None:
        self.records[approval.approval_id] = approval

    def get(self, approval_id: str, organization_id: str | None = None):
        approval = self.records.get(approval_id)
        if approval is not None and organization_id not in (None, approval.organization_id):
            return None
        return approval

    def transition(self, approval: Approval, expected_status: ApprovalStatus, event) -> bool:
        current = self.records.get(approval.approval_id)
        if current is None or current.status is not expected_status:
            return False
        self.records[approval.approval_id] = approval
        return True


class FakeComputerBackend:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.title = "Fixture window"
        self.element_name = "Document"
        self.runtime_ids = {"edit-1": [42, 1], "check-1": [42, 2]}
        self.dpi = 144
        self.value = ""
        self.checked = False
        self.scroll_y = 0
        self.other_scroll_y = 0
        self.scroll_other_only = False
        self.perform_calls: list[dict[str, object]] = []
        self.raise_after_dispatch = False
        self.apply_before_raise = False
        self.disconnect_after_dispatch = False
        self.disconnected = False
        self.apply_action = True
        self.sensitive = False

    def capability(self) -> dict[str, object]:
        return {
            "status": "AVAILABLE" if self.available else "UNAVAILABLE",
            "os": "windows",
            "session_id": 1 if self.available else None,
            "desktop_name": "Default" if self.available else None,
            "reason_code": None if self.available else "COMPUTER_SESSION_UNAVAILABLE",
        }

    def observe(self, window_ref: str) -> dict[str, object]:
        if self.disconnected:
            raise ConnectionError("fixture disconnected")
        if not self.available:
            raise RuntimeError("session unavailable")
        return {
            "window_ref": window_ref,
            "title": self.title,
            "process_id": 700,
            "bounds": [10, 20, 810, 620],
            "dpi": self.dpi,
            "elements": [
                {
                    "element_id": "edit-1",
                    "runtime_id": self.runtime_ids["edit-1"],
                    "control_type": "Edit",
                    "name": self.element_name,
                    "value": self.value,
                    "sensitive": self.sensitive,
                    "enabled": True,
                    "bounds": [30, 60, 700, 500],
                    "scroll_percent": self.scroll_y,
                },
                {
                    "element_id": "check-1",
                    "runtime_id": self.runtime_ids["check-1"],
                    "control_type": "CheckBox",
                    "name": "Fixture option",
                    "value": self.checked,
                    "sensitive": False,
                    "enabled": True,
                    "bounds": [20, 530, 200, 560],
                    "scroll_percent": self.other_scroll_y,
                },
            ],
            "scroll_y": max(self.scroll_y, self.other_scroll_y),
        }

    def perform(self, window_ref: str, action: Mapping[str, object], element_id: str) -> None:
        self.perform_calls.append(dict(action))
        if self.raise_after_dispatch:
            self.value = str(action["text"]) if self.apply_before_raise else "possibly-applied"
            if self.disconnect_after_dispatch:
                self.disconnected = True
            raise OSError("fixture disconnect after dispatch")
        if not self.apply_action:
            return
        if action["kind"] == "type":
            self.value = str(action["text"])
        elif action["kind"] == "click":
            self.checked = not self.checked
        elif action["kind"] == "scroll":
            if self.scroll_other_only:
                self.other_scroll_y = max(
                    0, min(100, self.other_scroll_y + (10 if action["delta_y"] > 0 else -10))
                )
            else:
                self.scroll_y = max(
                    0, min(100, self.scroll_y + (10 if action["delta_y"] > 0 else -10))
                )


def _make_runtime(
    *,
    backend: FakeComputerBackend | None = None,
    deny_actions: bool = False,
    approval_service=None,
) -> tuple[ComputerRuntime, FakeComputerBackend, list[EventRecord], list[dict[str, object]], object]:
    computer = backend or FakeComputerBackend()
    events: list[EventRecord] = []
    evidence: list[dict[str, object]] = []
    approvals = approval_service or RecordingApproval()
    rules = [
        PolicyRule(
            rule_id="computer-read",
            action="computer.observe",
            risk=RiskLevel.LOW,
            decision=PolicyOutcome.ALLOW,
            organization_id="org-1",
            permission="computer.read",
        ),
        PolicyRule(
            rule_id="computer-capability",
            action="computer.capability",
            risk=RiskLevel.LOW,
            decision=PolicyOutcome.ALLOW,
            organization_id="org-1",
            permission="computer.read",
        ),
        PolicyRule(
            rule_id="computer-act",
            action="computer.act",
            risk=RiskLevel.CRITICAL,
            decision=PolicyOutcome.DENY if deny_actions else PolicyOutcome.ALLOW,
            organization_id="org-1",
            permission="computer.act",
        ),
    ]

    def save_evidence(record: dict[str, object]) -> str:
        evidence.append(deepcopy(record))
        return f"computer-evidence-{len(evidence)}"

    runtime = ComputerRuntime(
        identity=RecordingIdentity(),
        policy=PolicyEngine(rules, policy_version="computer-fixture-v1"),
        approvals=approvals,
        idempotency_store=InMemoryIdempotencyStore(),
        event_sink=events.append,
        evidence_sink=save_evidence,
        backend=computer,
    )
    return runtime, computer, events, evidence, approvals


def _context(**overrides: object) -> dict[str, object]:
    return {
        "organization_id": "org-1",
        "trace_id": "trace-computer-1",
        "privacy": "INTERNAL",
        "approval_id": "approved-1",
        "target_approver_id": "reviewer-1",
        "action_summary": "Run a reviewed local UI fixture action",
        **overrides,
    }


def _observe(runtime: ComputerRuntime) -> dict[str, object]:
    result = runtime.observe(
        "fixture-window", actor={"subject_id": "agent-1"}, context=_context()
    )
    assert result.verification_status is VerificationStatus.VERIFIED
    return result.output  # type: ignore[return-value]


def _act(
    runtime: ComputerRuntime,
    observation: Mapping[str, object],
    *,
    kind: str = "type",
    idempotency_key: str = "computer-act-1",
    context_overrides: Mapping[str, object] | None = None,
    **values: object,
):
    if kind == "type" and "text" not in values:
        values["text"] = "fixture typed value"
    elements = observation["elements"]
    assert isinstance(elements, list) and elements
    element = next(item for item in elements if item["role"] == ("CheckBox" if kind == "click" else "Edit"))
    return runtime.act(
        {
            "kind": kind,
            "window_ref": observation["window_ref"],
            "observation_id": observation["observation_id"],
            "element_ref": element["element_ref"],
            **values,
        },
        actor={"subject_id": "agent-1"},
        context=_context(**(dict(context_overrides) if context_overrides else {})),
        idempotency_key=idempotency_key,
    )


def test_typed_action_can_be_approved_from_a_public_context_and_executed():
    approval_service = ApprovalService(
        repository=MemoryApprovalRepository(),
        authorize_approver=lambda **_: True,
        approval_ttl=timedelta(minutes=5),
    )
    runtime, backend, _, _, _ = _make_runtime(approval_service=approval_service)
    observation = _observe(runtime)
    element = next(item for item in observation["elements"] if item["role"] == "Edit")
    action = {
        "kind": "type",
        "window_ref": observation["window_ref"],
        "observation_id": observation["observation_id"],
        "element_ref": element["element_ref"],
        "text": "approved fixture marker",
    }
    actor = {"subject_id": "agent-1"}
    base_context = _context(approval_id=None)

    approval_context, policy_decision = runtime.prepare_approval_context(
        action, actor=actor, context=base_context
    )

    assert policy_decision.decision is PolicyOutcome.REQUIRE_APPROVAL
    action_params = approval_context["action_params"]
    assert "text" not in action_params
    assert "approved fixture marker" not in json.dumps(approval_context)
    assert action_params["text_digest"]
    approval = approval_service.request_approval(approval_context, policy_decision)
    approval_service.decide(
        approval.approval_id, approver_id="reviewer-1", decision="APPROVE"
    )

    result = runtime.act(
        action,
        actor=actor,
        context=_context(approval_id=approval.approval_id),
        idempotency_key="computer-approved-type-1",
    )

    assert result.verification_status is VerificationStatus.VERIFIED
    assert backend.value == "approved fixture marker"


def test_observation_is_scoped_redacted_and_marked_as_untrusted_ui_content():
    runtime, _, events, evidence, _ = _make_runtime()

    observation = _observe(runtime)

    assert observation["trust"] == "UNTRUSTED_UI_CONTENT"
    assert observation["window_ref"] == "fixture-window"
    assert observation["dpi"] == 144
    assert "value" not in observation["elements"][0]
    assert events[-1].event_type == "TOOL_EXECUTION_COMPLETED"


def test_dynamic_window_and_element_labels_are_not_retained_in_evidence():
    backend = FakeComputerBackend()
    backend.title = "Customer Ada Lovelace — Account 48291"
    backend.element_name = "Invoice for Northwind / 48291"
    runtime, _, events, evidence, _ = _make_runtime(backend=backend)

    observation = _observe(runtime)
    serialized_evidence = json.dumps(evidence, ensure_ascii=False)
    serialized_audit = json.dumps(
        [event.payload for event in events], ensure_ascii=False
    )

    assert observation["title"] == backend.title
    assert observation["elements"][0]["name"] == backend.element_name
    assert observation["trust"] == "UNTRUSTED_UI_CONTENT"
    assert backend.title not in serialized_evidence
    assert backend.element_name not in serialized_evidence
    assert backend.title not in serialized_audit
    assert backend.element_name not in serialized_audit
    assert evidence[-1]["trace_id"] == "trace-computer-1"


def test_observation_cache_does_not_retain_raw_control_text():
    runtime, backend, _, _, _ = _make_runtime()
    backend.value = "private document contents"

    _observe(runtime)

    assert "private document contents" not in repr(runtime._executor._latest)


def test_critical_computer_action_requires_approval_before_ui_dispatch():
    runtime, backend, _, _, approvals = _make_runtime()
    observation = _observe(runtime)

    with pytest.raises(ToolExecutionError) as error:
        _act(runtime, observation, context_overrides={"approval_id": None})

    assert error.value.code == "APPROVAL_REQUIRED"
    assert backend.perform_calls == []
    assert approvals.calls == []


def test_policy_deny_prevents_ui_dispatch_even_with_approval_id():
    runtime, backend, events, _, approvals = _make_runtime(deny_actions=True)
    observation = _observe(runtime)

    with pytest.raises(ToolExecutionError) as error:
        _act(runtime, observation)

    assert error.value.code == "POLICY_DENIED"
    assert backend.perform_calls == []
    assert approvals.calls == []
    assert events[-1].event_type == "TOOL_EXECUTION_DENIED"


def test_action_reobserves_and_verifies_typed_value_without_evidence_leak():
    runtime, backend, events, evidence, approvals = _make_runtime()
    observation = _observe(runtime)

    result = _act(runtime, observation, text="fixture text")

    assert result.verification_status is VerificationStatus.VERIFIED
    assert result.output["status"] == "VERIFIED"
    assert len(backend.perform_calls) == 1
    assert approvals.calls[0][0] == "approved-1"
    assert "fixture text" not in repr(approvals.calls)
    assert approvals.calls[0][1]["action_params"]["text_digest"] != "fixture text"
    assert "fixture text" not in repr(evidence)
    assert "fixture text" not in repr(events)


def test_click_is_verified_by_target_state_change():
    runtime, backend, _, _, _ = _make_runtime()
    observation = _observe(runtime)

    result = _act(runtime, observation, kind="click")

    assert result.verification_status is VerificationStatus.VERIFIED
    assert result.output["status"] == "VERIFIED"
    assert len(backend.perform_calls) == 1


def test_changed_window_snapshot_rejects_stale_observation_before_dispatch():
    runtime, backend, _, _, _ = _make_runtime()
    observation = _observe(runtime)
    backend.title = "A different window state"

    result = _act(runtime, observation)

    assert result.verification_status is VerificationStatus.FAILED
    assert result.output["error_code"] == "COMPUTER_OBSERVATION_STALE"
    assert backend.perform_calls == []


def test_changed_dpi_rejects_previously_observed_target():
    runtime, backend, _, _, _ = _make_runtime()
    observation = _observe(runtime)
    backend.dpi = 192

    result = _act(runtime, observation)

    assert result.output["error_code"] == "COMPUTER_OBSERVATION_STALE"
    assert backend.perform_calls == []


def test_recreated_uia_control_rejects_old_observation_and_approval():
    runtime, backend, _, _, _ = _make_runtime()
    observation = _observe(runtime)
    backend.runtime_ids["edit-1"] = [42, 99]

    result = _act(runtime, observation)

    assert result.verification_status is VerificationStatus.FAILED
    assert result.output["error_code"] == "COMPUTER_OBSERVATION_STALE"
    assert backend.perform_calls == []


def test_observation_without_uia_runtime_identity_fails_closed():
    runtime, backend, _, _, _ = _make_runtime()
    backend.runtime_ids["edit-1"] = None

    result = runtime.observe(
        "fixture-window", actor={"subject_id": "agent-1"}, context=_context()
    )

    assert result.output["status"] == "FAILED"
    assert result.output["error_code"] == "COMPUTER_OBSERVATION_INVALID"


def test_unverified_post_action_state_is_not_reported_as_success_or_retried():
    runtime, backend, events, _, _ = _make_runtime()
    observation = _observe(runtime)
    backend.apply_action = False

    result = _act(runtime, observation)

    assert result.verification_status is VerificationStatus.FAILED
    assert result.output["status"] == "UNVERIFIED"
    assert result.output["retryable"] is False
    assert len(backend.perform_calls) == 1
    assert events[-1].event_type == "TOOL_EXECUTION_UNVERIFIED"


def test_scroll_verification_uses_the_requested_scroll_container():
    runtime, backend, _, _, _ = _make_runtime()
    observation = _observe(runtime)
    backend.scroll_other_only = True

    result = _act(runtime, observation, kind="scroll", delta_y=120)

    assert result.verification_status is VerificationStatus.FAILED
    assert result.output["status"] == "UNVERIFIED"
    assert len(backend.perform_calls) == 1


def test_scroll_on_the_observed_container_is_verified():
    runtime, backend, _, _, _ = _make_runtime()
    observation = _observe(runtime)

    result = _act(runtime, observation, kind="scroll", delta_y=120)

    assert result.verification_status is VerificationStatus.VERIFIED
    assert backend.scroll_y == 10


def test_active_remote_session_is_interactive_and_session_zero_is_not():
    from shuliu_runtime.computer.win32_backend import _is_interactive_session_id

    assert _is_interactive_session_id(7)
    assert not _is_interactive_session_id(0)
    assert not _is_interactive_session_id(0xFFFFFFFF)


def test_backend_disconnect_after_dispatch_is_unknown_and_never_retried():
    runtime, backend, events, _, _ = _make_runtime()
    observation = _observe(runtime)
    backend.raise_after_dispatch = True
    backend.disconnect_after_dispatch = True

    with pytest.raises(ToolExecutionError) as error:
        _act(runtime, observation)

    assert error.value.code == "TOOL_EXECUTION_UNKNOWN"
    assert len(backend.perform_calls) == 1
    assert events[-1].event_type == "TOOL_EXECUTION_UNKNOWN"


def test_dispatch_error_reobserves_and_verifies_before_declaring_unknown():
    runtime, backend, events, _, _ = _make_runtime()
    observation = _observe(runtime)
    backend.raise_after_dispatch = True
    backend.apply_before_raise = True

    result = _act(runtime, observation, text="fixture typed value")

    assert result.verification_status is VerificationStatus.VERIFIED
    assert result.output["status"] == "VERIFIED"
    assert len(backend.perform_calls) == 1
    assert events[-1].event_type == "TOOL_EXECUTION_COMPLETED"


def test_locked_or_unknown_session_refuses_side_effects():
    runtime, backend, _, _, _ = _make_runtime(backend=FakeComputerBackend(available=False))

    capability = runtime.capability(actor={"subject_id": "agent-1"}, context=_context())
    result = runtime.act(
        {
            "kind": "click",
            "window_ref": "fixture-window",
            "observation_id": "known",
            "element_ref": "button-1",
        },
        actor={"subject_id": "agent-1"},
        context=_context(),
        idempotency_key="computer-locked-1",
    )

    assert capability.output["status"] == "UNAVAILABLE"
    assert result.output["status"] == "UNAVAILABLE"
    assert result.output["error_code"] == "COMPUTER_SESSION_UNAVAILABLE"
    assert backend.perform_calls == []


def test_runtime_recovers_after_session_returns_without_replaying_blocked_action():
    runtime, backend, _, _, _ = _make_runtime()
    first_observation = _observe(runtime)
    backend.available = False

    blocked = _act(
        runtime,
        first_observation,
        idempotency_key="computer-interrupted-1",
    )

    assert blocked.output["status"] == "UNAVAILABLE"
    assert backend.perform_calls == []

    backend.available = True
    recovered_observation = _observe(runtime)
    completed = _act(
        runtime,
        recovered_observation,
        idempotency_key="computer-recovered-1",
    )

    assert completed.verification_status is VerificationStatus.VERIFIED
    assert len(backend.perform_calls) == 1


def test_win32_capability_marks_locked_or_uncertain_desktop_unavailable():
    backend = Win32Backend(
        session_query=lambda: {
            "status": "UNAVAILABLE",
            "reason_code": "COMPUTER_SESSION_LOCKED",
            "session_id": 1,
            "desktop_name": "Winlogon",
        }
    )

    capability = backend.capability()

    assert capability["status"] == "UNAVAILABLE"
    assert capability["reason_code"] == "COMPUTER_SESSION_LOCKED"
    assert capability["session_id"] == 1


@pytest.mark.parametrize(
    "session, expected_code",
    [
        ({"status": "AVAILABLE", "session_id": 0, "desktop_name": "Default"}, "COMPUTER_SESSION_NOT_INTERACTIVE"),
        ({"status": "AVAILABLE", "session_id": 7, "desktop_name": "Winlogon"}, "COMPUTER_SESSION_LOCKED"),
    ],
)
def test_win32_capability_fails_closed_on_invalid_interactive_session(session, expected_code):
    backend = Win32Backend(session_query=lambda: session)

    capability = backend.capability()

    assert capability["status"] == "UNAVAILABLE"
    assert capability["reason_code"] == expected_code


def test_ufo_operator_prediction_cannot_bypass_policy_or_approval():
    runtime, backend, _, _, approvals = _make_runtime(deny_actions=True)
    observation = _observe(runtime)
    edit = observation["elements"][0]

    with pytest.raises(ToolExecutionError) as error:
        runtime.act_operator_prediction(
            {"operation": "click", "element_ref": edit["element_ref"]},
            observation,
            actor={"subject_id": "agent-1"},
            context=_context(),
            idempotency_key="computer-ufo-denied-1",
        )

    assert error.value.code == "POLICY_DENIED"
    assert backend.perform_calls == []
    assert approvals.calls == []


def test_type_refuses_a_sensitive_ui_control_before_dispatch():
    runtime, backend, _, evidence, _ = _make_runtime()
    backend.sensitive = True
    observation = _observe(runtime)

    result = _act(runtime, observation, text="must-not-be-typed")

    assert result.verification_status is VerificationStatus.FAILED
    assert result.output["error_code"] == "COMPUTER_SENSITIVE_TARGET_DENIED"
    assert backend.perform_calls == []
    assert "must-not-be-typed" not in repr(evidence)


def test_ufo_operator_converts_only_observed_semantic_targets():
    runtime, _, _, _, _ = _make_runtime()
    observation = _observe(runtime)
    target = observation["elements"][0]
    operator = UfoOperator()

    action = operator.to_action(
        {
            "operation": "type_text",
            "element_ref": target["element_ref"],
            "text": "approved fixture text",
        },
        observation,
    )

    assert action == {
        "kind": "type",
        "window_ref": "fixture-window",
        "observation_id": observation["observation_id"],
        "element_ref": target["element_ref"],
        "text": "approved fixture text",
    }


@pytest.mark.parametrize(
    "prediction",
    [
        {"operation": "run_shell", "command": "whoami"},
        {"operation": "click", "element_ref": "not-observed"},
    ],
)
def test_ufo_operator_rejects_commands_and_unobserved_targets(prediction):
    runtime, _, _, _, _ = _make_runtime()
    observation = _observe(runtime)

    with pytest.raises(UfoOperatorError):
        UfoOperator().to_action(prediction, observation)


def test_ufo_operator_does_not_downgrade_double_click_to_single_click():
    runtime, _, _, _, _ = _make_runtime()
    observation = _observe(runtime)
    target = observation["elements"][0]

    with pytest.raises(UfoOperatorError) as error:
        UfoOperator().to_action(
            {"operation": "left_double", "element_ref": target["element_ref"]},
            observation,
        )

    assert error.value.code == "COMPUTER_OPERATOR_ACTION_UNSUPPORTED"


def test_ufo_operator_rejects_malformed_observation_with_typed_error():
    with pytest.raises(UfoOperatorError):
        UfoOperator().to_action({"operation": "click"}, None)  # type: ignore[arg-type]


def test_ufo_operator_rejects_coordinates_outside_current_observation():
    runtime, _, _, _, _ = _make_runtime()
    observation = _observe(runtime)

    with pytest.raises(UfoOperatorError):
        UfoOperator().to_action(
            {"operation": "click", "x": 790, "y": 590}, observation
        )


def test_ufo_operator_resolves_coordinate_prediction_to_a_fresh_uia_element():
    runtime, _, _, _, _ = _make_runtime()
    observation = _observe(runtime)
    target = observation["elements"][0]

    action = UfoOperator().to_action(
        {"operation": "click", "x": 80, "y": 100}, observation
    )

    assert action["kind"] == "click"
    assert action["element_ref"] == target["element_ref"]
    assert action["observation_id"] == observation["observation_id"]
