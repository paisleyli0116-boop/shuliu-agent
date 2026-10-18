from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

from .capability import ModelCapability, ModelProviderConfig


LOCAL_ONLY = "LOCAL_ONLY"


class ModelHealth(Protocol):
    def is_healthy(self, provider_id: str) -> bool: ...


class ModelBudget(Protocol):
    """Budget port; reserve must be atomic for the referenced budget."""

    def reserve(
        self,
        organization_id: str,
        budget_ref: str,
        provider_id: str,
        capability: str,
        trace_id: str,
    ) -> bool: ...

    def settle(
        self,
        organization_id: str,
        budget_ref: str,
        usage: dict[str, int],
        cost: float | None,
    ) -> None: ...

    def release(
        self, organization_id: str, budget_ref: str, provider_id: str, trace_id: str
    ) -> None: ...


HealthCheck = Callable[[str], bool]


def route_candidates(
    providers: Iterable[ModelProviderConfig],
    *,
    capability: ModelCapability,
    privacy: str,
    health: ModelHealth | HealthCheck,
) -> tuple[ModelProviderConfig, ...]:
    candidates: list[ModelProviderConfig] = []
    for provider in providers:
        if capability.value not in provider.capabilities:
            continue
        if privacy == LOCAL_ONLY and not provider.is_local:
            continue
        try:
            healthy = (
                health.is_healthy(provider.provider_id)
                if hasattr(health, "is_healthy")
                else health(provider.provider_id)
            )
        except Exception:
            healthy = False
        if healthy:
            candidates.append(provider)
    return tuple(sorted(candidates, key=lambda provider: (provider.priority, provider.provider_id)))
