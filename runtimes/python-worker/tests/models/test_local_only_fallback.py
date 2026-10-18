from __future__ import annotations

import pytest

from shuliu_runtime.models.capability import ModelProviderConfig
from shuliu_runtime.models.gateway import ModelGateway, ModelGatewayError
from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.rules import PolicyRule
from tests.fakes.fake_model import FakeModel, MemoryBudget, MemoryHealth, MemoryModelCalls


def test_local_only_never_falls_back_to_remote_provider() -> None:
    providers = (
        ModelProviderConfig("local", "local/model", frozenset({"fast_chat"}), 1, True),
        ModelProviderConfig("remote", "remote/model", frozenset({"fast_chat"}), 2, False),
    )
    fake = FakeModel(failures={"local": RuntimeError("local unavailable")})
    policy = PolicyEngine(
        [
            PolicyRule(
                rule_id="allow-local",
                action="model.invoke.fast_chat.local",
                risk="LOW",
                decision="ALLOW",
                organization_id="org-a",
                permission="foundation.model.invoke",
                resource_type="model_provider",
                classification="LOCAL_ONLY",
                privacy="LOCAL_ONLY",
            ),
            PolicyRule(
                rule_id="allow-remote",
                action="model.invoke.fast_chat.remote",
                risk="LOW",
                decision="ALLOW",
                organization_id="org-a",
                permission="foundation.model.invoke",
                resource_type="model_provider",
                classification="LOCAL_ONLY",
                privacy="LOCAL_ONLY",
            ),
        ],
        policy_version="policy-v1",
    )
    gateway = ModelGateway(
        providers=providers,
        adapter=fake,
        policy=policy,
        budget=MemoryBudget(),
        health=MemoryHealth(("local", "remote")),
        model_calls=MemoryModelCalls(),
        fallback_enabled=True,
    )

    with pytest.raises(ModelGatewayError) as error:
        gateway.complete(
            {
                "capability": "fast_chat",
                "privacy": "LOCAL_ONLY",
                "budget_ref": "budget://org-a/default",
                "input": "private",
                "trace_id": "trace-01",
                "organization_id": "org-a",
                "subject_id": "user-01",
                "permissions": ("foundation.model.invoke",),
            }
        )

    assert error.value.code == "MODEL_PROVIDER_ERROR"
    assert [call.provider_id for call in fake.calls] == ["local"]


def test_local_only_request_with_only_remote_models_is_rejected_before_policy_or_provider() -> None:
    provider = ModelProviderConfig("remote", "remote/model", frozenset({"fast_chat"}), 1, False)
    fake = FakeModel()
    gateway = ModelGateway(
        providers=(provider,),
        adapter=fake,
        policy=PolicyEngine([], policy_version="policy-v1"),
        budget=MemoryBudget(),
        health=MemoryHealth(("remote",)),
        model_calls=MemoryModelCalls(),
    )

    with pytest.raises(ModelGatewayError) as error:
        gateway.complete(
            {
                "capability": "fast_chat",
                "privacy": "LOCAL_ONLY",
                "budget_ref": "budget://org-a/default",
                "input": "private",
                "trace_id": "trace-01",
                "organization_id": "org-a",
                "subject_id": "user-01",
                "permissions": ("foundation.model.invoke",),
            }
        )

    assert error.value.code == "MODEL_NO_ELIGIBLE_PROVIDER"
    assert fake.calls == []
