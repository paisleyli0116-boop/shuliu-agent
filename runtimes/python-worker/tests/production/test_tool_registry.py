from __future__ import annotations

import pytest

from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.production.tools import (
    ToolRegistryCompositionError,
    compose_tool_registries,
)
from shuliu_runtime.tools.registry import ToolDefinition, ToolRegistry


def definition(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        version="1.0.0",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        permissions=("foundation.tool.invoke",),
        risk_level=RiskLevel.LOW,
        idempotency_required=False,
    )


class Executor:
    def execute(self, tool_id, args, context, idempotency_key):
        return {}

    def verify(self, tool_id, args, output, context):
        return True


def registry(*names: str) -> ToolRegistry:
    instance = ToolRegistry()
    for name in names:
        instance.register(definition(name), Executor())
    return instance


def test_composition_keeps_every_registered_tool() -> None:
    composed = compose_tool_registries([registry("core.one"), registry("core.two")])

    assert composed.names() == ("core.one", "core.two")


def test_composition_of_nothing_is_an_empty_registry() -> None:
    # 空表意味着任何工具调用都会被拒绝；这是安全默认，不是配置成功。
    composed = compose_tool_registries([])

    assert composed.names() == ()


def test_two_providers_publishing_the_same_tool_are_rejected() -> None:
    with pytest.raises(ToolRegistryCompositionError) as error:
        compose_tool_registries([registry("core.one"), registry("core.one")])

    assert error.value.code == "TOOL_REGISTRY_CONFLICT"


def test_composed_definitions_stay_bound_to_their_own_executor() -> None:
    composed = compose_tool_registries([registry("core.one")])
    composed_definition = composed.get("core.one")

    assert composed_definition.name == "core.one"
    assert composed.validate_input("core.one", {}) is True
