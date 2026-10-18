from __future__ import annotations

from datetime import UTC, datetime

import pytest
from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.policy.rules import PolicyDecision, PolicyOutcome
from shuliu_runtime.skills.capability_proxy import SkillCapabilityProxy
from shuliu_runtime.tools.broker import (
    IdentityPrincipal,
    ToolBroker,
    ToolExecutionStatus,
    ToolResult,
    VerificationStatus,
)
from shuliu_runtime.tools.registry import ToolDefinition, ToolRegistry


class RecordingBroker:
    def __init__(self):
        self.calls = []

    def execute(self, tool_id, args, actor, context, *, idempotency_key):
        self.calls.append(
            (tool_id, dict(args), dict(actor), dict(context), idempotency_key)
        )
        return ToolResult(
            tool_id=tool_id,
            tool_version="1.0.0",
            execution_status=ToolExecutionStatus.SUCCEEDED,
            verification_status=VerificationStatus.VERIFIED,
            output={"ok": True},
            error_code=None,
            policy_decision=PolicyOutcome.ALLOW,
            policy_version="test-v1",
            policy_rule_id="test-rule",
        )


class ToolPermissionLookup:
    def get(self, tool_id):
        if tool_id != "foundation.echo":
            raise KeyError(tool_id)
        return ToolDefinition(
            name=tool_id,
            version="1.0.0",
            input_schema={"type": "object", "additionalProperties": True},
            output_schema={"type": "object"},
            permissions=("foundation.echo",),
            risk_level=RiskLevel.LOW,
            idempotency_required=False,
        )


def _proxy(broker=None, *, permissions=("foundation.echo",)):
    return SkillCapabilityProxy(
        broker=broker or RecordingBroker(),
        registry=ToolPermissionLookup(),
        actor={"subject_id": "host-agent", "organization_id": "org-1"},
        context={
            "organization_id": "org-1",
            "trace_id": "trusted-trace",
            "privacy": "INTERNAL",
            "approval_id": "host-approved-1",
        },
        approved_permissions=permissions,
        idempotency_key="host-run-key",
    )


def test_skill_tool_request_is_forwarded_only_through_broker_with_host_context():
    broker = RecordingBroker()
    proxy = _proxy(broker)

    result = proxy.dispatch(
        {
            "type": "tool.request",
            "request_id": "request-1",
            "tool_id": "foundation.echo",
            "args": {"text": "hello"},
        }
    )

    assert result == {
        "type": "tool.response",
        "request_id": "request-1",
        "output": {"ok": True},
    }
    assert len(broker.calls) == 1
    tool_id, args, actor, context, key = broker.calls[0]
    assert tool_id == "foundation.echo"
    assert args == {"text": "hello"}
    assert actor == {"subject_id": "host-agent", "organization_id": "org-1"}
    assert context["trace_id"] == "trusted-trace"
    assert key and key != "host-run-key"

    proxy.dispatch(
        {
            "type": "tool.request",
            "request_id": "retry-with-new-transport-id",
            "tool_id": "foundation.echo",
            "args": {"text": "hello"},
        }
    )
    assert broker.calls[1][4] == key


def test_skill_cannot_escalate_permission_or_supply_approval_context():
    broker = RecordingBroker()
    proxy = _proxy(broker)

    denied = proxy.dispatch(
        {
            "type": "tool.request",
            "request_id": "request-2",
            "tool_id": "foundation.admin.delete",
            "args": {},
        }
    )
    assert denied["error"]["code"] == "SKILL_PERMISSION_DENIED"
    assert broker.calls == []

    with pytest.raises(ValueError, match="unsupported fields"):
        proxy.dispatch(
            {
                "type": "tool.request",
                "request_id": "request-3",
                "tool_id": "foundation.echo",
                "args": {},
                "approval_id": "skill-self-approved",
            }
        )


def test_tool_policy_deny_is_enforced_by_broker_without_executor_call():
    registry = ToolRegistry()

    class Executor:
        calls = 0

        def execute(self, *_args):
            self.calls += 1
            return {"ok": True}

        def verify(self, *_args):
            return True

    executor = Executor()
    registry.register(
        ToolDefinition(
            name="foundation.echo",
            version="1.0.0",
            input_schema={"type": "object", "additionalProperties": True},
            output_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            permissions=("foundation.echo",),
            risk_level=RiskLevel.LOW,
            idempotency_required=False,
        ),
        executor,
    )

    class Identity:
        def resolve(self, _actor):
            return IdentityPrincipal("host-agent", "org-1", ("foundation.echo",))

    class DenyPolicy:
        def evaluate(self, **request):
            context = request["context"]
            return PolicyDecision(
                decision=PolicyOutcome.DENY,
                risk=RiskLevel.LOW,
                rule_id="deny-all-test",
                policy_version="test-v1",
                reason="test denial",
                trace_id=context["trace_id"],
            )

    broker = ToolBroker(
        registry=registry,
        identity=Identity(),
        policy=DenyPolicy(),
        approvals=None,
        event_sink=lambda _event: None,
        clock=lambda: datetime.now(UTC),
    )
    proxy = _proxy(broker)
    proxy.registry = registry

    result = proxy.dispatch(
        {
            "type": "tool.request",
            "request_id": "request-4",
            "tool_id": "foundation.echo",
            "args": {},
        }
    )

    assert result["error"]["code"] == "POLICY_DENIED"
    assert executor.calls == 0


def test_unverified_tool_result_is_not_forwarded_to_skill():
    class UnverifiedBroker:
        def execute(self, *_args, **_kwargs):
            return ToolResult(
                tool_id="foundation.echo",
                tool_version="1.0.0",
                execution_status=ToolExecutionStatus.SUCCEEDED,
                verification_status=VerificationStatus.FAILED,
                output={"ok": True},
                error_code="TOOL_RESULT_UNVERIFIED",
                policy_decision=PolicyOutcome.ALLOW,
                policy_version="test-v1",
                policy_rule_id="test-rule",
            )

    proxy = _proxy(UnverifiedBroker())
    result = proxy.dispatch(
        {
            "type": "tool.request",
            "request_id": "request-unverified",
            "tool_id": "foundation.echo",
            "args": {"text": "hello"},
        }
    )

    assert result == {
        "type": "tool.response",
        "request_id": "request-unverified",
        "error": {"code": "TOOL_RESULT_UNVERIFIED"},
    }


def test_policy_deny_tool_result_is_not_forwarded_as_success():
    class DeniedBroker:
        def execute(self, *_args, **_kwargs):
            return ToolResult(
                tool_id="foundation.echo",
                tool_version="1.0.0",
                execution_status=ToolExecutionStatus.SUCCEEDED,
                verification_status=VerificationStatus.VERIFIED,
                output={"ok": True},
                error_code=None,
                policy_decision=PolicyOutcome.DENY,
                policy_version="test-v1",
                policy_rule_id="deny-all",
            )

    result = _proxy(DeniedBroker()).dispatch(
        {
            "type": "tool.request",
            "request_id": "request-denied-result",
            "tool_id": "foundation.echo",
            "args": {"text": "hello"},
        }
    )

    assert result == {
        "type": "tool.response",
        "request_id": "request-denied-result",
        "error": {"code": "POLICY_DENIED"},
    }
