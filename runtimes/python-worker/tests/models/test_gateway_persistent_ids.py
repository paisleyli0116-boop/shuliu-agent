from __future__ import annotations

import pytest

from shuliu_runtime.models.capability import ModelProviderConfig
from shuliu_runtime.models.gateway import ModelGateway, ModelGatewayError, ModelProviderError, ModelRequest, ProviderResult
from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.rules import PolicyRule


class _Budget:
    def __init__(self):
        self.events = []

    def reserve(self, organization_id, budget_ref, provider_id, capability, trace_id, *, call_id):
        self.events.append(("reserve", call_id))
        return True

    def settle(self, organization_id, budget_ref, call_id, usage, cost):
        self.events.append(("settle", call_id))

    def release(self, organization_id, budget_ref, call_id):
        self.events.append(("release", call_id))

    def mark_unknown(self, organization_id, budget_ref, call_id):
        self.events.append(("unknown", call_id))


class _Health:
    def is_healthy(self, provider_id):
        return True


class _Calls:
    def __init__(self):
        self.records = []

    def record(self, call):
        self.records.append(call)


class _Adapter:
    def __init__(self, error):
        self.error = error

    def invoke(self, provider, request, api_key=None):
        raise self.error


def _gateway(adapter, budget, calls):
    provider = ModelProviderConfig("provider-a", "model-a", frozenset({"fast_chat"}), 1, False)
    policy = PolicyEngine(
        [
            PolicyRule(
                rule_id="allow-model",
                action="model.invoke.fast_chat.provider-a",
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
    return ModelGateway(
        providers=(provider,), adapter=adapter, policy=policy, budget=budget,
        health=_Health(), model_calls=calls,
    )


def _request():
    return ModelRequest(
        call_id="logical-call-1", capability="fast_chat", privacy="INTERNAL",
        budget_ref="budget://org-a/default", input="hello", trace_id="trace-1",
        organization_id="org-a", subject_id="user-a", permissions=("foundation.model.invoke",),
    )


def test_unknown_provider_outcome_is_not_released_and_call_id_is_shared():
    budget = _Budget()
    calls = _Calls()
    gateway = _gateway(
        _Adapter(ModelProviderError("MODEL_TIMEOUT", request_status="unknown", failure_stage="provider_transport")),
        budget,
        calls,
    )

    with pytest.raises(ModelGatewayError) as error:
        gateway.complete(_request())

    assert error.value.request_status == "unknown"
    assert [event[0] for event in budget.events] == ["reserve", "unknown"]
    assert budget.events[0][1] == budget.events[1][1]
    assert calls.records[0].status == "UNKNOWN"
    assert calls.records[0].call_id == budget.events[0][1]


def test_known_not_sent_provider_failure_releases_same_call_reservation():
    budget = _Budget()
    calls = _Calls()
    gateway = _gateway(
        _Adapter(ModelProviderError("MODEL_CREDENTIAL_UNAVAILABLE", request_status="not_sent", failure_stage="secret_resolution")),
        budget,
        calls,
    )

    with pytest.raises(ModelGatewayError):
        gateway.complete(_request())

    assert [event[0] for event in budget.events] == ["reserve", "release"]
    assert budget.events[0][1] == budget.events[1][1]
    assert calls.records[0].status == "FAILED"


def test_unknown_provider_outcome_never_triggers_fallback():
    budget = _Budget()
    calls = _Calls()

    class _FirstProviderUnknown:
        def __init__(self):
            self.invoked = []

        def invoke(self, provider, request, api_key=None):
            self.invoked.append(provider.provider_id)
            raise ModelProviderError(
                "MODEL_TIMEOUT", request_status="unknown", failure_stage="provider_transport"
            )

    adapter = _FirstProviderUnknown()
    providers = (
        ModelProviderConfig("provider-a", "model-a", frozenset({"fast_chat"}), 1, False),
        ModelProviderConfig("provider-b", "model-b", frozenset({"fast_chat"}), 2, False),
    )
    policy = PolicyEngine(
        [
            PolicyRule(
                rule_id=f"allow-{provider}",
                action=f"model.invoke.fast_chat.{provider}",
                risk="LOW",
                decision="ALLOW",
                organization_id="org-a",
                permission="foundation.model.invoke",
                resource_type="model_provider",
                classification="INTERNAL",
                privacy="INTERNAL",
            )
            for provider in ("provider-a", "provider-b")
        ],
        policy_version="policy-v1",
    )
    gateway = ModelGateway(
        providers=providers, adapter=adapter, policy=policy, budget=budget,
        health=_Health(), model_calls=calls, fallback_enabled=True,
    )

    with pytest.raises(ModelGatewayError) as error:
        gateway.complete(_request())

    assert error.value.request_status == "unknown"
    assert adapter.invoked == ["provider-a"]
    assert [event[0] for event in budget.events] == ["reserve", "unknown"]


def test_unrecognized_provider_usage_field_is_rejected_before_settlement():
    class _InvalidUsageAdapter:
        def invoke(self, provider, request, api_key=None):
            return ProviderResult(output="response", usage={"untrusted_payload_fragment": 1}, cost=0.01)

    budget = _Budget()
    calls = _Calls()
    gateway = _gateway(_InvalidUsageAdapter(), budget, calls)

    with pytest.raises(ModelGatewayError) as error:
        gateway.complete(_request())

    assert error.value.code == "MODEL_PROVIDER_RESPONSE_INVALID"
    assert [event[0] for event in budget.events] == ["reserve", "release"]
    assert calls.records[0].usage == {}
    assert calls.records[0].error_code == "MODEL_PROVIDER_RESPONSE_INVALID"
