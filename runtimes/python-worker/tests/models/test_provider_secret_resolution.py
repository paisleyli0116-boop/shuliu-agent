from __future__ import annotations

import json
import gc
import weakref

import shuliu_runtime.models as models
from shuliu_runtime.models.gateway import ModelGateway
from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.rules import PolicyRule
from shuliu_runtime.security.secret_store import SecretResolver
from tests.fakes.fake_model import FakeModel, MemoryBudget, MemoryHealth


class MemorySecrets:
    def __init__(self, values):
        self.values = values

    def get(self, reference):
        return _EphemeralSecret(self.values[str(reference)])

    def put(self, reference, value):
        self.values[str(reference)] = value

    def delete(self, reference):
        self.values.pop(reference, None)


class CredentialObservingAdapter:
    def __init__(self):
        self.credential_refs = []

    def invoke(self, provider, request, api_key=None):
        self.credential_refs.append(weakref.ref(api_key))
        return FakeModel().invoke(provider, request, api_key)


class _EphemeralSecret(str):
    pass


class CredentialLifetimeModelCalls:
    def __init__(self, adapter):
        self.adapter = adapter

    def record(self, call):
        gc.collect()
        assert all(reference() is None for reference in self.adapter.credential_refs), (
            "Gateway retained plaintext credentials after adapter invocation"
        )


def test_secret_resolver_supplies_plaintext_only_to_gateway_adapter_call():
    loader = getattr(models, "load_provider_profiles", None)
    service_type = getattr(models, "ProviderConnectionService", None)
    context_type = getattr(models, "ProviderConnectionContext", None)
    assert callable(loader), "Task 28 must expose validated provider profiles"
    assert callable(service_type), "Task 28 must expose the connection test service"
    assert callable(context_type), "Task 28 must require trusted authorization context"

    profile, = loader(json.dumps([{
        "provider_id": "provider-a",
        "model": "openai/gpt-4.1-mini",
        "capabilities": ["fast_chat"],
        "priority": 1,
        "is_local": False,
        "secret_ref": "secret://os/providers/provider-a/key",
    }]))
    credential = "sk-credential-marker"
    secrets = MemorySecrets({profile.secret_ref: credential})
    adapter = CredentialObservingAdapter()
    policy = PolicyEngine(
        [PolicyRule(
            rule_id="allow-provider-connection-test",
            action="model.invoke.fast_chat.provider-a",
            risk="LOW",
            decision="ALLOW",
            organization_id="org-a",
            permission="foundation.model.connection_test",
            resource_type="model_provider",
            classification="PUBLIC",
            privacy="PUBLIC",
        )],
        policy_version="test-policy-v1",
    )
    resolver = SecretResolver({"os": secrets})
    assert resolver.get(profile.secret_ref) == credential
    gateway = ModelGateway(
        providers=(profile.to_model_provider_config(),),
        adapter=adapter,
        policy=policy,
        budget=MemoryBudget(),
        health=MemoryHealth(("provider-a",)),
        model_calls=CredentialLifetimeModelCalls(adapter),
        secret_resolver=resolver,
    )
    context = context_type(
        organization_id="org-a",
        subject_id="user-a",
        permissions=("foundation.model.connection_test",),
        budget_ref="budget://org-a/provider-connection-test",
    )
    service = service_type((profile,), gateway, context)

    result = service.test_provider_connection("provider-a", "fast_chat")

    assert result.status == "connected", result
    assert len(adapter.credential_refs) == 1
    assert adapter.credential_refs[0]() is None
    assert credential not in repr(result)
    assert credential not in repr(gateway._providers)
    assert secrets.values[profile.secret_ref] == credential
