from uuid import uuid4

import pytest

from shuliu_runtime.models import connection
from shuliu_runtime.models.config import ProviderProfile
from shuliu_runtime.models.gateway import ProviderResult
from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.rules import PolicyRule
from shuliu_runtime.production.identity import ProductionIdentity


@pytest.fixture()
def fake_provider(monkeypatch):
    calls = []
    profile = ProviderProfile(
        provider_id="fixture", model="fixture/chat", capabilities=frozenset({"fast_chat"}),
        priority=1, is_local=True, secret_ref=None,
    )

    class Adapter:
        def invoke(self, provider, request, api_key=None):
            calls.append(request)
            return ProviderResult(output="fixture reply", usage={"total_tokens": 1},
                                  cost=0.0, model_version=provider.model)

    monkeypatch.setattr(connection, "load_provider_profiles", lambda: (profile,))
    monkeypatch.setattr(connection, "LiteLLMAdapter", lambda **kwargs: Adapter())
    monkeypatch.setattr(connection, "OSSecretStore", lambda: object())
    monkeypatch.setenv("SHULIU_ORGANIZATION_ID", "actual-org")
    return calls


def complete(runtime):
    trace = str(uuid4())
    result = runtime.complete_chat(
        provider_id="fixture", model_id="fixture/chat",
        messages=[{"role": "user", "content": "fixture input"}],
        request_id=str(uuid4()), trace_id=trace, external_processing_confirmed=True,
    )
    assert result["trace_id"] == trace
    return result


def principal(permissions=("foundation.model.chat",)):
    return ProductionIdentity("actual-user", "actual-org", permissions, "PUBLIC")


def policy(decision):
    return PolicyEngine([PolicyRule(
        rule_id="deployed-chat", action="model.invoke.fast_chat.fixture", risk="LOW",
        decision=decision, organization_id="actual-org", permission="foundation.model.chat",
    )], policy_version="deployed-v2")


def test_missing_production_dependencies_cannot_self_grant_chat(fake_provider):
    result = complete(connection.create_provider_runtime_from_environment())
    assert result["safe_error_code"] == "CHAT_SERVICE_UNAVAILABLE"
    assert result["request_status"] == "not_sent"
    assert fake_provider == []


@pytest.mark.parametrize(("permissions", "decision"), [
    (("computer.read",), "ALLOW"), (("foundation.model.chat",), "DENY"),
])
def test_chat_uses_deployed_permissions_and_policy(fake_provider, permissions, decision):
    runtime = connection.create_provider_runtime_from_environment(
        chat_principal=principal(permissions), chat_policy=policy(decision),
    )
    result = complete(runtime)
    assert result["safe_error_code"] == "MODEL_POLICY_DENIED"
    assert result["request_status"] == "not_sent"
    assert fake_provider == []


def test_allowed_chat_uses_actual_principal_not_canary_identity(fake_provider):
    runtime = connection.create_provider_runtime_from_environment(
        chat_principal=principal(), chat_policy=policy("ALLOW"),
    )
    assert complete(runtime)["response"] == "fixture reply"
    assert len(fake_provider) == 1
    assert fake_provider[0].subject_id == "actual-user"
    assert fake_provider[0].organization_id == "actual-org"
    assert fake_provider[0].permissions == ("foundation.model.chat",)
