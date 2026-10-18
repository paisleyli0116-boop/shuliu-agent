from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from shuliu_runtime.models.gateway import ProviderResult


FakeModelResult = ProviderResult


@dataclass(frozen=True)
class FakeCall:
    provider_id: str
    model: str


class FakeModel:
    def __init__(self, *, responses=None, failures=None):
        self.responses = responses or {}
        self.failures = failures or {}
        self.calls: list[FakeCall] = []
        self.received_credential: bool = False

    def invoke(self, provider, request, api_key=None):
        self.calls.append(FakeCall(provider.provider_id, provider.model))
        self.received_credential = self.received_credential or bool(api_key)
        failure = self.failures.get(provider.provider_id)
        if failure is not None:
            raise failure
        return self.responses.get(
            provider.provider_id,
            FakeModelResult(output="ok", usage={}, cost=0.0),
        )


class MemoryBudget:
    def __init__(self, *, allowed: bool = True):
        self.allowed = allowed
        self.reservations: list[tuple[str, str, str, str]] = []
        self.settlements: list[tuple[str, str, object]] = []

    def reserve(self, organization_id, budget_ref, provider_id, capability, trace_id):
        if not self.allowed:
            return False
        self.reservations.append((organization_id, budget_ref, provider_id, capability))
        return True

    def settle(self, organization_id, budget_ref, usage, cost):
        self.settlements.append((organization_id, budget_ref, cost))

    def release(self, organization_id, budget_ref, provider_id, trace_id):
        self.reservations = [
            item
            for item in self.reservations
            if not (item[0] == organization_id and item[1] == budget_ref and item[2] == provider_id)
        ]


class MemoryHealth:
    def __init__(self, healthy):
        self.healthy = set(healthy)

    def is_healthy(self, provider_id):
        return provider_id in self.healthy


@dataclass(frozen=True)
class RecordedModelCall:
    provider_id: str
    requested_capability: str
    resolved_provider: str
    error_code: str | None


class MemoryModelCalls:
    def __init__(self):
        self.records: list[RecordedModelCall] = []

    def record(self, call):
        self.records.append(
            RecordedModelCall(
                provider_id=call.resolved_provider,
                requested_capability=call.requested_capability,
                resolved_provider=call.resolved_provider,
                error_code=call.error_code,
            )
        )
