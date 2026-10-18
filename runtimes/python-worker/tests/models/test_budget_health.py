from __future__ import annotations

import pytest

from shuliu_runtime.models.capability import ModelProviderConfig
from shuliu_runtime.models.gateway import ModelGateway, ModelGatewayError, ModelRequest
from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.rules import PolicyRule
from tests.fakes.fake_model import FakeModel, FakeModelResult, MemoryBudget, MemoryHealth, MemoryModelCalls


def _policy() -> PolicyEngine:
    return PolicyEngine(
        [
            PolicyRule(
                rule_id=f"allow-{provider_id}",
                action=f"model.invoke.reasoning_high.{provider_id}",
                risk="LOW",
                decision="ALLOW",
                organization_id="org-a",
                permission="foundation.model.invoke",
                resource_type="model_provider",
                classification="INTERNAL",
                privacy="INTERNAL",
            )
            for provider_id in ("primary", "backup")
        ],
        policy_version="policy-v1",
    )


def _request() -> ModelRequest:
    return ModelRequest(
        capability="reasoning_high",
        privacy="INTERNAL",
        budget_ref="budget://org-a/default",
        input="analyze",
        trace_id="trace-02",
        organization_id="org-a",
        subject_id="user-02",
        permissions=("foundation.model.invoke",),
    )


def test_exhausted_budget_blocks_all_providers_before_execution() -> None:
    providers = (
        ModelProviderConfig("primary", "remote/primary", frozenset({"reasoning_high"}), 1, False),
        ModelProviderConfig("backup", "remote/backup", frozenset({"reasoning_high"}), 2, False),
    )
    fake = FakeModel()
    budget = MemoryBudget(allowed=False)
    gateway = ModelGateway(
        providers=providers,
        adapter=fake,
        policy=_policy(),
        budget=budget,
        health=MemoryHealth(("primary", "backup")),
        model_calls=MemoryModelCalls(),
    )

    with pytest.raises(ModelGatewayError) as error:
        gateway.complete(_request())

    assert error.value.code == "MODEL_BUDGET_EXHAUSTED"
    assert fake.calls == []
    assert budget.reservations == []


def test_unhealthy_primary_uses_healthy_fallback_and_records_actual_route() -> None:
    providers = (
        ModelProviderConfig("primary", "remote/primary", frozenset({"reasoning_high"}), 1, False),
        ModelProviderConfig("backup", "remote/backup", frozenset({"reasoning_high"}), 2, False),
    )
    fake = FakeModel(
        responses={"backup": FakeModelResult(output="reasoned", usage={"total_tokens": 12}, cost=0.05)}
    )
    budget = MemoryBudget()
    calls = MemoryModelCalls()
    gateway = ModelGateway(
        providers=providers,
        adapter=fake,
        policy=_policy(),
        budget=budget,
        health=MemoryHealth(("backup",)),
        model_calls=calls,
        fallback_enabled=True,
    )

    result = gateway.complete(_request())

    assert result.resolved_provider == "backup"
    assert [call.provider_id for call in fake.calls] == ["backup"]
    assert calls.records[0].resolved_provider == "backup"
    assert calls.records[0].requested_capability == "reasoning_high"
    assert budget.reservations == [("org-a", "budget://org-a/default", "backup", "reasoning_high")]
    assert budget.settlements == [("org-a", "budget://org-a/default", 0.05)]


def test_health_failure_and_missing_budget_backend_fail_closed() -> None:
    provider = ModelProviderConfig("primary", "remote/primary", frozenset({"reasoning_high"}), 1, False)
    fake = FakeModel()
    with pytest.raises(TypeError):
        ModelGateway(
            providers=(provider,),
            adapter=fake,
            policy=_policy(),
            budget=None,
            health=MemoryHealth(("primary",)),
            model_calls=MemoryModelCalls(),
        )

    gateway = ModelGateway(
        providers=(provider,),
        adapter=fake,
        policy=_policy(),
        budget=MemoryBudget(),
        health=MemoryHealth(()),
        model_calls=MemoryModelCalls(),
    )
    with pytest.raises(ModelGatewayError) as error:
        gateway.complete(_request())
    assert error.value.code == "MODEL_NO_ELIGIBLE_PROVIDER"
    assert fake.calls == []
