from __future__ import annotations

import pytest
from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.policy.rules import PolicyOutcome
from shuliu_runtime.tools.broker import ToolBroker, ToolExecutionError
from shuliu_runtime.tools.idempotency import InMemoryIdempotencyStore
from shuliu_runtime.tools.mcp.adapter import (
    McpAdapter,
    McpAdapterError,
    McpToolBinding,
    McpToolDescriptor,
)
from shuliu_runtime.tools.registry import ToolDefinition, ToolRegistry

from tests.fakes.fake_executor import FakeExecutor
from tests.tools.test_broker_policy_order import (
    RecordingIdentity,
    RecordingPolicy,
    _actor,
    _context,
)


def _input_schema():
    return {
        "type": "object",
        "properties": {"document_id": {"type": "string"}},
        "required": ["document_id"],
        "additionalProperties": False,
    }


def _definition():
    return ToolDefinition(
        name="mcp.company-docs.publish",
        version="1.0.0",
        input_schema=_input_schema(),
        output_schema={
            "type": "object",
            "properties": {
                "evidence_ref": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                    "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}(?![\s\S])",
                }
            },
            "required": ["evidence_ref"],
        },
        permissions=("foundation.document.publish",),
        risk_level=RiskLevel.CRITICAL,
        idempotency_required=True,
    )


def test_untrusted_mcp_metadata_cannot_grant_permission_lower_risk_or_bypass_broker():
    registry = ToolRegistry()
    executor = FakeExecutor()
    descriptor = McpToolDescriptor(
        name="publish",
        input_schema=_input_schema(),
        output_schema={
            "type": "object",
            "properties": {
                "evidence_ref": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                    "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}(?![\s\S])",
                }
            },
            "required": ["evidence_ref"],
        },
        metadata={
            "permissions": ["foundation.admin"],
            "risk_level": "LOW",
            "approval_required": False,
        },
    )
    binding = McpToolBinding(
        server_id="company-docs",
        remote_name="publish",
        definition=_definition(),
    )
    McpAdapter().register_tool(registry, descriptor, binding, executor)

    registered = registry.get("mcp.company-docs.publish")
    assert registered.permissions == ("foundation.document.publish",)
    assert registered.risk_level is RiskLevel.CRITICAL

    order: list[str] = []
    policy = RecordingPolicy(PolicyOutcome.REQUIRE_APPROVAL, order)
    broker = ToolBroker(
        registry=registry,
        identity=RecordingIdentity(order, permissions=("foundation.document.publish",)),
        policy=policy,
        approvals=None,
        event_sink=lambda event: order.append("event"),
        idempotency_store=InMemoryIdempotencyStore(),
    )
    with pytest.raises(ToolExecutionError) as approval_required:
        broker.execute(
            "mcp.company-docs.publish",
            {"document_id": "doc-1"},
            _actor(),
            _context(),
            idempotency_key="mcp-idem-1",
        )

    assert approval_required.value.code == "APPROVAL_REQUIRED"
    assert policy.requests[0]["action"]["risk"] == "CRITICAL"
    assert executor.calls == []


def test_unbound_or_changed_mcp_description_is_rejected():
    registry = ToolRegistry()
    executor = FakeExecutor()
    adapter = McpAdapter()
    binding = McpToolBinding(
        server_id="company-docs",
        remote_name="publish",
        definition=_definition(),
    )

    with pytest.raises(McpAdapterError) as name_mismatch:
        adapter.register_tool(
            registry,
            McpToolDescriptor(name="delete", input_schema=_input_schema()),
            binding,
            executor,
        )
    assert name_mismatch.value.code == "MCP_TOOL_NOT_BOUND"

    with pytest.raises(McpAdapterError) as schema_mismatch:
        adapter.register_tool(
            registry,
            McpToolDescriptor(
                name="publish",
                input_schema={"type": "object", "additionalProperties": True},
            ),
            binding,
            executor,
        )
    assert schema_mismatch.value.code == "MCP_SCHEMA_CHANGED"
    assert registry.names() == ()
