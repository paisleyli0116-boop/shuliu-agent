from __future__ import annotations

import pytest

from shuliu_runtime.models.capability import ModelCapability, ModelProviderConfig
from shuliu_runtime.models.gateway import ModelGateway, ModelGatewayError, ModelProviderError, ModelRequest
from shuliu_runtime.models.providers.litellm_adapter import LiteLLMAdapter
from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.rules import PolicyRule
from tests.fakes.fake_model import FakeModel, FakeModelResult, MemoryBudget, MemoryHealth, MemoryModelCalls


def _request(**overrides: object) -> ModelRequest:
    values: dict[str, object] = {
        "capability": "fast_chat",
        "privacy": "INTERNAL",
        "budget_ref": "budget://org-a/default",
        "input": "hello",
        "trace_id": "trace-01",
        "organization_id": "org-a",
        "subject_id": "user-01",
        "permissions": ("foundation.model.invoke",),
    }
    values.update(overrides)
    return ModelRequest(**values)


def _provider(provider_id: str, *, priority: int, capabilities: tuple[str, ...] = ("fast_chat",), local: bool = False) -> ModelProviderConfig:
    return ModelProviderConfig(
        provider_id=provider_id,
        model=f"test/{provider_id}",
        capabilities=frozenset(capabilities),
        priority=priority,
        is_local=local,
    )


def _policy(*allowed: str) -> PolicyEngine:
    return PolicyEngine(
        [
            PolicyRule(
                rule_id=f"allow-{provider_id}",
                action=f"model.invoke.fast_chat.{provider_id}",
                risk="LOW",
                decision="ALLOW",
                organization_id="org-a",
                permission="foundation.model.invoke",
                resource_type="model_provider",
                classification="INTERNAL",
                privacy="INTERNAL",
            )
            for provider_id in allowed
        ],
        policy_version="policy-v1",
    )


def _gateway(
    providers: tuple[ModelProviderConfig, ...],
    model: FakeModel,
    *,
    policy: PolicyEngine | None = None,
    healthy: tuple[str, ...] | None = None,
    budget_allowed: bool = True,
    model_calls: MemoryModelCalls | None = None,
) -> ModelGateway:
    return ModelGateway(
        providers=providers,
        adapter=model,
        policy=policy or _policy(*(provider.provider_id for provider in providers)),
        budget=MemoryBudget(allowed=budget_allowed),
        health=MemoryHealth(healthy if healthy is not None else tuple(p.provider_id for p in providers)),
        model_calls=model_calls or MemoryModelCalls(),
        fallback_enabled=True,
    )


def test_capability_catalog_contains_foundation_capabilities() -> None:
    assert {item.value for item in ModelCapability} == {
        "fast_chat",
        "reasoning_high",
        "vision",
        "embedding",
    }


def test_gateway_uses_configured_priority_and_records_resolved_route() -> None:
    providers = (_provider("backup", priority=20), _provider("primary", priority=10))
    records = MemoryModelCalls()
    fake = FakeModel(
        responses={
            "primary": FakeModelResult(output="answer", usage={"total_tokens": 7}, cost=0.02),
        }
    )
    gateway = _gateway(providers, fake, model_calls=records)

    result = gateway.complete(_request())

    assert result.output == "answer"
    assert result.requested_capability == "fast_chat"
    assert result.resolved_provider == "primary"
    assert result.resolved_model == "test/primary"
    assert result.usage == {"total_tokens": 7}
    assert result.cost == 0.02
    assert [call.provider_id for call in fake.calls] == ["primary"]
    assert records.records[0].resolved_provider == "primary"


def test_complete_for_provider_rejects_mismatched_embedding_model_before_budget_or_provider() -> None:
    provider = ModelProviderConfig(
        "embed-provider", "embed-model-v1", frozenset({"embedding"}), 1, False
    )
    fake = FakeModel()
    budget = MemoryBudget()
    gateway = ModelGateway(
        providers=(provider,),
        adapter=fake,
        policy=_policy("embed-provider"),
        budget=budget,
        health=MemoryHealth(("embed-provider",)),
        model_calls=MemoryModelCalls(),
    )

    with pytest.raises(ModelGatewayError) as error:
        gateway.complete_for_provider(
            "embed-provider",
            _request(capability="embedding", input=["text"]),
            model="other-embedding-model",
        )

    assert error.value.code == "MODEL_ROUTE_MISMATCH"
    assert fake.calls == []
    assert budget.reservations == []


def test_complete_for_provider_pins_embedding_route_and_records_gateway_usage() -> None:
    provider = ModelProviderConfig(
        "embed-provider", "embed-model-v1", frozenset({"embedding"}), 1, False
    )
    fake = FakeModel(
        responses={
            "embed-provider": FakeModelResult(
                output=[[0.1, 0.2]], usage={"total_tokens": 4}, cost=0.001
            )
        }
    )
    records = MemoryModelCalls()
    budget = MemoryBudget()
    policy = PolicyEngine(
        [
            PolicyRule(
                rule_id="allow-embedding",
                action="model.invoke.embedding.embed-provider",
                risk="LOW",
                decision="ALLOW",
                organization_id="org-a",
                permission="foundation.model.invoke",
                resource_type="model_provider",
                classification="INTERNAL",
                privacy="INTERNAL",
            )
        ],
        policy_version="policy-v1",
    )
    gateway = ModelGateway(
        providers=(provider,),
        adapter=fake,
        policy=policy,
        budget=budget,
        health=MemoryHealth(("embed-provider",)),
        model_calls=records,
        fallback_enabled=True,
    )

    result = gateway.complete_for_provider(
        "embed-provider",
        _request(capability="embedding", input=["one"]),
        model="embed-model-v1",
    )

    assert result.output == [[0.1, 0.2]]
    assert result.resolved_provider == "embed-provider"
    assert result.resolved_model == "embed-model-v1"
    assert [call.provider_id for call in fake.calls] == ["embed-provider"]
    assert records.records[0].requested_capability == "embedding"
    assert len(budget.settlements) == 1


def test_unknown_or_unsupported_capability_fails_closed_without_provider_call() -> None:
    fake = FakeModel()
    gateway = _gateway((_provider("primary", priority=1, capabilities=("vision",)),), fake)

    with pytest.raises(ModelGatewayError) as error:
        gateway.complete(_request())

    assert error.value.code == "MODEL_CAPABILITY_UNSUPPORTED"
    assert fake.calls == []


def test_missing_policy_allow_fails_closed_before_provider_call() -> None:
    fake = FakeModel()
    gateway = _gateway(
        (_provider("primary", priority=1),),
        fake,
        policy=PolicyEngine([], policy_version="policy-v1"),
    )

    with pytest.raises(ModelGatewayError) as error:
        gateway.complete(_request())

    assert error.value.code == "MODEL_POLICY_DENIED"
    assert fake.calls == []


def test_each_fallback_candidate_gets_its_own_policy_decision() -> None:
    providers = (_provider("primary", priority=1), _provider("secondary", priority=2))
    fake = FakeModel(failures={"primary": ModelProviderError(
        "MODEL_PROVIDER_SERVER_ERROR", request_status="http_response_received",
        failure_stage="provider_http", http_status=503,
    )})
    records = MemoryModelCalls()
    policy = _policy("primary", "secondary")
    gateway = _gateway(providers, fake, policy=policy, model_calls=records)

    result = gateway.complete(_request())

    assert result.resolved_provider == "secondary"
    assert [call.provider_id for call in fake.calls] == ["primary", "secondary"]
    assert [call.resolved_provider for call in records.records] == ["primary", "secondary"]
    assert records.records[0].error_code == "MODEL_PROVIDER_SERVER_ERROR"
    assert records.records[1].error_code is None


def test_policy_denial_blocks_a_fallback_provider() -> None:
    providers = (_provider("primary", priority=1), _provider("secondary", priority=2))
    fake = FakeModel(failures={"primary": ModelProviderError(
        "MODEL_PROVIDER_SERVER_ERROR", request_status="http_response_received",
        failure_stage="provider_http", http_status=503,
    )})
    policy = _policy("primary")
    gateway = _gateway(providers, fake, policy=policy)

    with pytest.raises(ModelGatewayError) as error:
        gateway.complete(_request())

    assert error.value.code == "MODEL_POLICY_DENIED"
    assert "provider secret detail" not in str(error.value)
    assert [call.provider_id for call in fake.calls] == ["primary"]


def test_litellm_adapter_passes_secure_key_and_disables_sdk_retries() -> None:
    calls: list[dict[str, object]] = []

    def completion(**kwargs: object) -> object:
        calls.append(kwargs)
        return {
            "model": "provider/model-v2",
            "choices": [{"message": {"content": "answer"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            "_hidden_params": {"response_cost": 0.01},
        }

    adapter = LiteLLMAdapter(completion=completion, timeout_seconds=9)
    provider = ModelProviderConfig(
        "provider", "provider/model", frozenset({"fast_chat"}), 1, False
    )
    result = adapter.invoke(provider, _request(), api_key="resolved-secret")

    assert result.output == "answer"
    assert result.model_version == "provider/model-v2"
    assert result.usage == {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}
    assert result.cost == 0.01
    assert calls == [
        {
            "model": "provider/model",
            "messages": [{"role": "user", "content": "hello"}],
            "api_key": "resolved-secret",
            "timeout": 9,
            "num_retries": 0,
        }
    ]


def test_litellm_adapter_fails_closed_when_remote_secret_is_missing() -> None:
    called = False

    def completion(**_: object) -> object:
        nonlocal called
        called = True
        return {}

    adapter = LiteLLMAdapter(completion=completion)
    provider = ModelProviderConfig(
        "provider", "provider/model", frozenset({"fast_chat"}), 1, False
    )
    with pytest.raises(ModelProviderError) as error:
        adapter.invoke(provider, _request())

    assert error.value.code == "MODEL_CREDENTIAL_UNAVAILABLE"
    assert called is False


def test_litellm_rate_limit_error_is_mapped_without_exposing_provider_message() -> None:
    class RateLimitError(Exception):
        pass

    def completion(**_: object) -> object:
        raise RateLimitError("token=provider-secret")

    adapter = LiteLLMAdapter(completion=completion)
    provider = ModelProviderConfig(
        "provider", "provider/model", frozenset({"fast_chat"}), 1, False
    )

    with pytest.raises(ModelProviderError) as error:
        adapter.invoke(provider, _request(), api_key="resolved-secret")

    assert error.value.code == "MODEL_RATE_LIMITED"
    assert "provider-secret" not in str(error.value)


def test_litellm_adapter_uses_embedding_endpoint_for_embedding_capability() -> None:
    calls: list[dict[str, object]] = []

    def embedding(**kwargs: object) -> object:
        calls.append(kwargs)
        return {
            "model": "provider/embed-v1",
            "data": [{"embedding": [0.1, 0.2]}],
            "usage": {"total_tokens": 3},
        }

    adapter = LiteLLMAdapter(embedding=embedding)
    provider = ModelProviderConfig(
        "provider", "provider/embed", frozenset({"embedding"}), 1, False
    )
    result = adapter.invoke(
        provider,
        _request(capability="embedding", input="vectorize this"),
        api_key="resolved-secret",
    )

    assert result.output == [[0.1, 0.2]]
    assert result.usage == {"total_tokens": 3}
    assert calls == [
        {
            "model": "provider/embed",
            "input": "vectorize this",
            "api_key": "resolved-secret",
            "timeout": 60.0,
            "num_retries": 0,
        }
    ]


def test_gateway_resolves_provider_credential_through_secret_store() -> None:
    class SecretResolverFake:
        def get(self, reference: str) -> str:
            assert reference == "secret://os/models/provider"
            return "test-secret"

    provider = ModelProviderConfig(
        "primary",
        "provider/model",
        frozenset({"fast_chat"}),
        1,
        False,
        secret_ref="secret://os/models/provider",
    )
    fake = FakeModel()
    gateway = ModelGateway(
        providers=(provider,),
        adapter=fake,
        policy=_policy("primary"),
        budget=MemoryBudget(),
        health=MemoryHealth(("primary",)),
        model_calls=MemoryModelCalls(),
        secret_resolver=SecretResolverFake(),
    )

    gateway.complete(_request())

    assert fake.received_credential is True


def test_provider_secret_reference_requires_secret_store() -> None:
    provider = ModelProviderConfig(
        "primary",
        "provider/model",
        frozenset({"fast_chat"}),
        1,
        False,
        secret_ref="secret://os/models/provider",
    )
    fake = FakeModel()
    gateway = _gateway((provider,), fake)

    with pytest.raises(ModelGatewayError) as error:
        gateway.complete(_request())

    assert error.value.code == "MODEL_CREDENTIAL_UNAVAILABLE"
    assert fake.calls == []
