import pytest
from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.rules import PolicyRule, PolicyOutcome
from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.tools.broker import IdentityPrincipal
from shuliu_runtime.tools.idempotency import InMemoryIdempotencyStore
from tests.fakes.fake_browser import FakeBrowser, LoopbackFixtureGate


class Identity:
    def resolve(self, actor):
        name = actor.get("subject_id")
        return IdentityPrincipal(name, "org-1", ("browser.read", "browser.act")) if name in ("alice", "bob") else None


class FixtureApproval:
    def __init__(self):
        self.calls = []
    def consume(self, approval_id, action_context):
        assert approval_id == "approved-fixture"
        self.calls.append(action_context)


def make_runtime(tmp_path, *, deny=False, gate=True, factory=None, ttl=60, origin="http://127.0.0.1:8765", timeout=15):
    try:
        from shuliu_runtime.browser.adapter import BrowserRuntime
    except ImportError:
        pytest.fail("Task 24 missing: BrowserRuntime must route browser operations through Tool Broker")
    backend, backends, events, evidence = FakeBrowser(), [], [], []
    def create_backend():
        value = factory() if factory else (backend if not backends else FakeBrowser())
        backends.append(value)
        return value
    def save_evidence(record):
        evidence.append(record)
        return "evidence-" + str(len(evidence))
    rules = []
    for op in ("launch", "observe", "close", "navigate", "click", "type", "scroll"):
        read = op in ("launch", "observe", "close")
        rules.append(PolicyRule(rule_id=op, action="browser." + op,
            risk=RiskLevel.LOW if read else RiskLevel.CRITICAL,
            decision=PolicyOutcome.DENY if deny else (PolicyOutcome.ALLOW if read else PolicyOutcome.REQUIRE_APPROVAL),
            organization_id="org-1"))
    approvals = FixtureApproval()
    runtime = BrowserRuntime(identity=Identity(), policy=PolicyEngine(rules, policy_version="fixture-v1"),
        approvals=approvals, idempotency_store=InMemoryIdempotencyStore(), event_sink=events.append,
        evidence_sink=save_evidence, backend_factory=create_backend, profile_root=tmp_path,
        allowed_origins=(origin,), execution_gate=LoopbackFixtureGate(origin) if gate else None,
        session_ttl=ttl, timeout=timeout)
    return runtime, backend, events, evidence, approvals


def call(runtime, operation, args=None, *, key=None, actor="alice", **overrides):
    context = {"organization_id": "org-1", "trace_id": "trace-browser", "privacy": "INTERNAL",
        "approval_id": "approved-fixture", "target_approver_id": "reviewer",
        "action_summary": "Controlled fixture browser operation", **overrides}
    return runtime.call(operation, args or {}, actor={"subject_id": actor}, context=context, idempotency_key=key)


def opened(runtime):
    launch = call(runtime, "launch")
    assert launch.verification_status.value == "VERIFIED"
    session = launch.output["session_id"]
    result = call(runtime, "navigate", {"session_id": session, "url": "http://127.0.0.1:8765/"}, key="navigate")
    assert result.verification_status.value == "VERIFIED"
    return session, call(runtime, "observe", {"session_id": session}).output["observation"]


def click_args(session, observation):
    return {"session_id": session, "observation_id": observation["observation_id"],
        "element_ref": observation["elements"][0]["element_ref"], "expected_text": "Counter: 1"}
