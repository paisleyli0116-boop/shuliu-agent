from __future__ import annotations

from collections.abc import Iterable

from shuliu_runtime.tools.registry import ToolRegistry


class ToolRegistryCompositionError(RuntimeError):
    """Two Tool providers disagree about who owns a reviewed Tool contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def compose_tool_registries(sources: Iterable[ToolRegistry]) -> ToolRegistry:
    """Merge reviewed Tool contracts from their owning providers.

    A duplicate name is refused rather than overwritten: silently letting the
    last provider win would make the effective Tool contract depend on
    composition order. An empty result is allowed and refuses every call, which
    keeps the runtime honest when no Tool provider is deployed.
    """
    composed = ToolRegistry()
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, ToolRegistry):
            raise TypeError("Tool composition requires ToolRegistry instances")
        for definition, executor in source.bindings():
            if definition.name in seen:
                raise ToolRegistryCompositionError("TOOL_REGISTRY_CONFLICT")
            seen.add(definition.name)
            composed.register(definition, executor)
    return composed
