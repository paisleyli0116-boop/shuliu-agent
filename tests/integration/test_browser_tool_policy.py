import pytest
from shuliu_runtime.tools.broker import ToolExecutionError
from tests.browser.support import call, click_args, make_runtime, opened

pytestmark = pytest.mark.integration


def test_policy_denial_cannot_launch_a_browser(tmp_path):
    runtime, backend, events, evidence, _ = make_runtime(tmp_path, deny=True)
    try:
        with pytest.raises(ToolExecutionError) as caught:
            call(runtime, "launch")
        assert caught.value.code == "POLICY_DENIED"
        assert backend.calls == [] and evidence == []
        assert events[-1].event_type == "TOOL_EXECUTION_DENIED"
    finally:
        runtime.close()


def test_approval_is_mandatory_and_unknown_arguments_are_rejected(tmp_path):
    runtime, backend, *_ = make_runtime(tmp_path)
    try:
        session, observation = opened(runtime)
        with pytest.raises(ToolExecutionError) as caught:
            call(runtime, "click", click_args(session, observation), key="not-approved", approval_id=None)
        assert caught.value.code == "APPROVAL_REQUIRED"
        with pytest.raises(ToolExecutionError) as malformed:
            call(runtime, "click", {**click_args(session, observation), "risk": "LOW", "secret_ref": "secret://x"}, key="injection")
        assert malformed.value.code == "TOOL_INPUT_INVALID"
        assert "click" not in backend.calls
    finally:
        runtime.close()


@pytest.mark.parametrize("change", [None, "params", "expired", "rejected"])
def test_real_approval_service_binds_browser_action_and_audits_consumption(tmp_path, change):
    from datetime import UTC, datetime, timedelta
    from shuliu_runtime.approval.service import ApprovalService, ApprovalStatus
    from shuliu_runtime.policy.rules import PolicyDecision, PolicyOutcome
    from shuliu_runtime.policy.risk import RiskLevel
    from tests.tools.test_broker_policy_order import MemoryApprovalRepository
    runtime, backend, _, _, _ = make_runtime(tmp_path)
    try:
        session, observation = opened(runtime)
        args = click_args(session, observation)
        now = [datetime(2026, 10, 19, tzinfo=UTC)]
        repository = MemoryApprovalRepository()
        approvals = ApprovalService(repository=repository,
            authorize_approver=lambda **values: values["approver_id"] == "reviewer",
            approval_ttl=timedelta(minutes=1), clock=lambda: now[0])
        context = {"organization_id": "org-1", "requester_id": "alice", "target_approver_id": "reviewer",
            "action_id": "browser.click", "action_summary": "Controlled fixture browser operation",
            "resource": {}, "risk": "CRITICAL", "action_params": args, "run_id": None, "trace_id": "trace-browser"}
        decision = PolicyDecision(decision=PolicyOutcome.REQUIRE_APPROVAL, policy_version="fixture-v1", rule_id="click",
            reason="fixture", trace_id="trace-browser", risk=RiskLevel.CRITICAL)
        approval = approvals.request_approval(context, decision)
        approvals.decide(approval.approval_id, approver_id="reviewer", decision="REJECT" if change == "rejected" else "APPROVE")
        runtime.broker._approvals = approvals
        if change == "params":
            args = {**args, "expected_text": "Another result"}
        if change == "expired":
            now[0] += timedelta(minutes=2)
        if change:
            with pytest.raises(ToolExecutionError):
                call(runtime, "click", args, key="bound", approval_id=approval.approval_id)
            assert "click" not in backend.calls
        else:
            assert call(runtime, "click", args, key="bound", approval_id=approval.approval_id).output["status"] == "VERIFIED"
            assert approvals.get(approval.approval_id).status == ApprovalStatus.CONSUMED
            assert repository.events[-1].trace_id == "trace-browser"
    finally:
        runtime.close()


def test_skill_capability_proxy_only_forwards_verified_browser_results(tmp_path):
    from shuliu_runtime.skills.capability_proxy import SkillCapabilityProxy
    runtime, backend, *_ = make_runtime(tmp_path)
    try:
        session, observation = opened(runtime)
        proxy = SkillCapabilityProxy(broker=runtime.broker, registry=runtime.registry,
            actor={"subject_id": "alice"}, context={"organization_id": "org-1", "trace_id": "trace-browser",
                "privacy": "INTERNAL", "approval_id": "approved-fixture", "target_approver_id": "reviewer",
                "action_summary": "Controlled fixture browser operation"},
            approved_permissions=("browser.act",), idempotency_key="host-key")
        backend.change_on_click = False
        result = proxy.dispatch({"type": "tool.request", "request_id": "request-1", "tool_id": "browser.click",
            "args": click_args(session, observation)})
        assert "output" not in result
        assert result["error"]["code"] == "TOOL_RESULT_UNVERIFIED"
        assert backend.calls.count("click") == 1
    finally:
        runtime.close()
