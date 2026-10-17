from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from shuliu_runtime.tools.executor import ToolExecutor
from shuliu_runtime.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolRegistryError,
)


class McpAdapterError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class McpToolDescriptor:
    name: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object] | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class McpToolBinding:
    server_id: str
    remote_name: str
    definition: ToolDefinition


class McpAdapter:
    """Binds discovered MCP descriptions to locally trusted Tool definitions."""

    def register_tool(
        self,
        registry: ToolRegistry,
        descriptor: McpToolDescriptor,
        binding: McpToolBinding,
        executor: ToolExecutor,
    ) -> None:
        if not isinstance(descriptor, McpToolDescriptor) or not isinstance(
            binding, McpToolBinding
        ):
            raise McpAdapterError(
                "MCP_TOOL_NOT_BOUND", "An explicit local binding is required"
            )
        if (
            not _non_empty(binding.server_id)
            or not _non_empty(binding.remote_name)
            or descriptor.name != binding.remote_name
            or binding.definition.name
            != f"mcp.{binding.server_id}.{binding.remote_name}"
        ):
            raise McpAdapterError(
                "MCP_TOOL_NOT_BOUND", "MCP tool does not match its local binding"
            )
        if not _same_schema(descriptor.input_schema, binding.definition.input_schema):
            raise McpAdapterError(
                "MCP_SCHEMA_CHANGED",
                "MCP input schema differs from the reviewed binding",
            )
        if descriptor.output_schema is not None and not _same_schema(
            descriptor.output_schema, binding.definition.output_schema
        ):
            raise McpAdapterError(
                "MCP_SCHEMA_CHANGED",
                "MCP output schema differs from the reviewed binding",
            )
        try:
            registry.register(binding.definition, executor)
        except ToolRegistryError as error:
            raise McpAdapterError(error.code, str(error)) from error


def _same_schema(left: object, right: object) -> bool:
    try:
        return json.dumps(
            left, sort_keys=True, separators=(",", ":"), allow_nan=False
        ) == json.dumps(right, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return False


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
