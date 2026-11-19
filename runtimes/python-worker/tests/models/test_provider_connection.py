from __future__ import annotations

import json
import threading
import pytest
from dataclasses import asdict

import shuliu_runtime.models as models
import shuliu_runtime.models.connection as provider_connection
from shuliu_runtime.models.gateway import ModelGateway
from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.rules import PolicyRule
from shuliu_runtime.security.secret_store import SecretResolver

class _ConnectionSecrets:
    def get(self, reference):
        return "sk-connection-test-marker"

    def put(self, reference, value):
        raise AssertionError("connection test must not write credentials")

    def delete(self, reference):
        raise AssertionError("connection test must not delete credentials")
from tests.fakes.fake_model import FakeModel, MemoryBudget, MemoryHealth, MemoryModelCalls


def _service_type():
    service_type = getattr(models, "ProviderConnectionService", None)
    assert callable(service_type), "Task 28 must provide a Gateway-backed connection test service"
    return service_type


def _profile():
    loader = getattr(models, "load_provider_profiles", None)
    assert callable(loader), "Task 28 must expose validated provider profiles"
    return loader(json.dumps([{
        "provider_id": "provider-a",
        "model": "openai/gpt-4.1-mini",
        "capabilities": ["fast_chat"],
        "priority": 1,
        "is_local": False,
        "secret_ref": "secret://os/providers/provider-a/key",
    }]))[0]


def _service(*, policy_allowed=True, budget_allowed=True, failure=None, canary_enabled=True):
    profile = _profile()
    provider = profile.to_model_provider_config()
    adapter = FakeModel(failures={"provider-a": failure} if failure else {})
    policy = PolicyEngine(
        [
            PolicyRule(
                rule_id="allow-provider-connection-test",
                action="model.invoke.fast_chat.provider-a",
                risk="LOW",
                decision="ALLOW",
                organization_id="org-a",
                permission="foundation.model.connection_test",
                resource_type="model_provider",
                classification="PUBLIC",
                privacy="PUBLIC",
            )
        ] if policy_allowed else [],
        policy_version="test-policy-v1",
    )
    budget = MemoryBudget(allowed=budget_allowed)
    gateway = ModelGateway(
        providers=(provider,),
        adapter=adapter,
        policy=policy,
        budget=budget,
        health=MemoryHealth(("provider-a",)),
        model_calls=MemoryModelCalls(),
        secret_resolver=SecretResolver({"os": _ConnectionSecrets()}),
        fallback_enabled=True,
    )
    context_type = getattr(models, "ProviderConnectionContext", None)
    assert callable(context_type), "connection test must receive trusted authorization context"
    context = context_type(
        organization_id="org-a",
        subject_id="user-a",
        permissions=("foundation.model.connection_test",),
        budget_ref="budget://org-a/provider-connection-test",
    )
    service = _service_type()((profile,), gateway, context, canary_enabled=canary_enabled)
    return service, adapter, budget


def test_canary_gate_blocks_before_gateway_when_not_explicitly_enabled():
    service, adapter, _ = _service(canary_enabled=False)

    result = service.test_provider_connection("provider-a", "fast_chat")

    assert result.status == "blocked"
    assert result.safe_error_code == "MODEL_CONNECTION_TEST_DISABLED"
    assert result.trace_id
    assert adapter.calls == []


def test_provider_ipc_cannot_bypass_the_disabled_canary_gate():
    service, adapter, _ = _service(canary_enabled=False)
    profile = _profile()
    runtime_type = getattr(models, "ProviderRuntime", None)
    handlers_factory = getattr(models, "create_provider_ipc_handlers", None)
    assert callable(runtime_type)
    assert callable(handlers_factory)
    runtime = runtime_type(
        (profile,),
        SecretResolver({"os": _ConnectionSecrets()}),
        service,
    )

    result = handlers_factory(runtime)["providers.test_connection"](
        {"provider_id": "provider-a", "capability": "fast_chat"}, threading.Event()
    )

    assert result["status"] == "blocked"
    assert result["safe_error_code"] == "MODEL_CONNECTION_TEST_DISABLED"
    assert adapter.calls == []


def test_runtime_factory_enables_canary_only_for_explicit_process_opt_in(monkeypatch):
    from shuliu_runtime.models.gateway import ModelGatewayError

    monkeypatch.delenv("SHULIU_MODEL_CONNECTION_TEST", raising=False)
    monkeypatch.setattr(provider_connection, "load_provider_profiles", lambda: (_profile(),))
    monkeypatch.setattr(provider_connection, "OSSecretStore", lambda: _ConnectionSecrets())
    calls = []

    def stop_before_provider(self, provider_id, request):
        calls.append((provider_id, request))
        raise ModelGatewayError("MODEL_CONNECTION_TEST_SENTINEL", "safe test sentinel")

    monkeypatch.setattr(provider_connection.ModelGateway, "complete_for_provider", stop_before_provider)
    disabled = provider_connection.create_provider_runtime_from_environment()
    denied = disabled.test_connection("provider-a", "fast_chat")
    assert denied["status"] == "blocked"
    assert denied["safe_error_code"] == "MODEL_CONNECTION_TEST_DISABLED"
    assert calls == []

    monkeypatch.setenv("SHULIU_MODEL_CONNECTION_TEST", "1")
    enabled = provider_connection.create_provider_runtime_from_environment()
    result = enabled.test_connection("provider-a", "fast_chat")
    assert result["status"] == "failed"
    assert result["safe_error_code"] == "MODEL_PROVIDER_ERROR"
    assert len(calls) == 1


def test_connection_uses_gateway_policy_budget_and_returns_only_safe_result():
    service, adapter, budget = _service()

    result = service.test_provider_connection("provider-a", "fast_chat")

    assert result.status == "connected", result
    assert result.provider_id == "provider-a"
    assert result.model == "openai/gpt-4.1-mini"
    assert result.capability == "fast_chat"
    assert result.trace_id
    assert result.safe_error_code is None
    assert adapter.calls == [type(adapter.calls[0])("provider-a", "openai/gpt-4.1-mini")]
    assert budget.settlements
    assert "output" not in asdict(result)
    assert "api_key" not in asdict(result)


def test_policy_denial_and_exhausted_budget_block_before_provider_call():
    denied, denied_adapter, _ = _service(policy_allowed=False)
    over_budget, budget_adapter, _ = _service(budget_allowed=False)

    denied_result = denied.test_provider_connection("provider-a", "fast_chat")
    budget_result = over_budget.test_provider_connection("provider-a", "fast_chat")

    assert denied_result.status == "blocked"
    assert denied_result.safe_error_code == "MODEL_POLICY_DENIED"
    assert budget_result.status == "blocked"
    assert budget_result.safe_error_code == "MODEL_BUDGET_EXHAUSTED"
    assert denied_adapter.calls == []
    assert budget_adapter.calls == []


def test_provider_failure_never_returns_provider_exception_text():
    service, adapter, _ = _service(failure=RuntimeError("sk-connection-secret"))

    result = service.test_provider_connection("provider-a", "fast_chat")

    assert result.status == "failed"
    assert result.safe_error_code == "MODEL_PROVIDER_ERROR"
    assert "sk-connection-secret" not in repr(result)
    assert len(adapter.calls) == 1


def test_connection_test_cannot_fallback_to_a_different_provider():
    assert hasattr(ModelGateway, "complete_for_provider"), (
        "Task 28 must let the gateway constrain a connection probe to its selected provider"
    )

def test_gateway_provider_scoped_call_does_not_fallback_after_failure():
    from shuliu_runtime.models.capability import ModelProviderConfig
    from shuliu_runtime.models.gateway import ModelGatewayError, ModelRequest
    from shuliu_runtime.policy.engine import PolicyEngine
    from shuliu_runtime.policy.rules import PolicyRule
    from tests.fakes.fake_model import FakeModel, MemoryBudget, MemoryHealth, MemoryModelCalls
    import pytest

    providers = (
        ModelProviderConfig("provider-a", "local/a", frozenset({"fast_chat"}), 1, True),
        ModelProviderConfig("provider-b", "local/b", frozenset({"fast_chat"}), 2, True),
    )
    adapter = FakeModel(failures={"provider-a": RuntimeError("provider-a failed")})
    policy = PolicyEngine(
        [
            PolicyRule(
                rule_id=f"allow-{provider.provider_id}",
                action=f"model.invoke.fast_chat.{provider.provider_id}",
                risk="LOW",
                decision="ALLOW",
                organization_id="org-a",
                permission="foundation.model.connection_test",
                resource_type="model_provider",
                classification="PUBLIC",
                privacy="PUBLIC",
            )
            for provider in providers
        ],
        policy_version="test-policy-v1",
    )
    gateway = ModelGateway(
        providers=providers,
        adapter=adapter,
        policy=policy,
        budget=MemoryBudget(),
        health=MemoryHealth(("provider-a", "provider-b")),
        model_calls=MemoryModelCalls(),
        secret_resolver=SecretResolver({"os": _ConnectionSecrets()}),
        fallback_enabled=True,
    )
    request = ModelRequest(
        capability="fast_chat",
        privacy="PUBLIC",
        budget_ref="budget://org-a/provider-connection-test",
        input="Reply with exactly OK.",
        trace_id="trace-connection-test",
        organization_id="org-a",
        subject_id="user-a",
        permissions=("foundation.model.connection_test",),
    )

    with pytest.raises(ModelGatewayError) as error:
        gateway.complete_for_provider("provider-a", request)

    assert error.value.code == "MODEL_PROVIDER_ERROR"
    assert [call.provider_id for call in adapter.calls] == ["provider-a"]

def test_provider_runtime_writes_credentials_only_through_secret_resolver_and_lists_metadata():
    runtime_type = getattr(models, "ProviderRuntime", None)
    assert callable(runtime_type), "Task 28 must connect provider settings to the Python SecretResolver"

    class RecordingResolver:
        def __init__(self):
            self.writes = []
            self.values = {}
            self.reads = []

        def get(self, reference):
            reference = str(reference)
            self.reads.append(reference)
            return self.values.get(reference)

        def put(self, reference, value):
            reference = str(reference)
            self.writes.append((reference, value))
            self.values[reference] = value

        def delete(self, reference):
            raise AssertionError("replacing a credential must not delete unrelated secrets")

    profile = _profile()
    resolver = RecordingResolver()
    service, _, _ = _service()
    runtime = runtime_type((profile,), resolver, service)

    catalog = runtime.list_profiles()
    result = runtime.save_profile("provider-a", "openai/gpt-4.1-mini", "sk-save-marker")

    assert catalog == {
        "providers": [{
            "id": "provider-a",
            "name": "provider-a",
            "models": [{
                "id": "openai/gpt-4.1-mini",
                "name": "openai/gpt-4.1-mini",
                "capabilities": ["fast_chat"],
            }],
            "selected_model_id": "openai/gpt-4.1-mini",
            "configured": False,
            "enabled": True,
        }]
    }
    reloaded_runtime = runtime_type((profile,), resolver, service)
    saved_catalog = reloaded_runtime.list_profiles()
    assert saved_catalog["providers"][0]["configured"] is True
    assert not any(reference == profile.secret_ref for reference in resolver.reads)
    assert resolver.writes[0] == (profile.secret_ref, "sk-save-marker")
    assert resolver.writes[-1][1] == "provider-configured-v1"
    assert "sk-save-marker" not in repr(catalog)
    assert "sk-save-marker" not in repr(result)
    assert result["configured"] is True


def test_provider_runtime_exposes_only_fixed_ipc_methods():
    from threading import Event
    from shuliu_runtime.models import connection

    create_handlers = getattr(connection, "create_provider_ipc_handlers", None)
    assert callable(create_handlers), "Task 28 must register fixed provider methods on Python IPC"
    profile = _profile()
    service, _, _ = _service()
    runtime_type = getattr(models, "ProviderRuntime", None)
    assert callable(runtime_type), "Task 28 must connect provider settings to the Python runtime"
    runtime = runtime_type((profile,), _ConnectionSecrets(), service)
    handlers = create_handlers(runtime)

    assert set(handlers) == {
        "providers.list",
        "providers.save_profile",
        "providers.replace_credential",
        "providers.test_connection",
    }
    assert handlers["providers.list"]({}, Event()) == runtime.list_profiles()
    with pytest.raises(ValueError):
        handlers["providers.list"]({"extra": "not-allowed"}, Event())


def test_empty_environment_builds_provider_runtime_without_a_real_provider(monkeypatch):
    from shuliu_runtime.models.connection import create_provider_runtime_from_environment

    monkeypatch.delenv("SHULIU_MODEL_PROFILES_JSON", raising=False)
    runtime = create_provider_runtime_from_environment()

    assert runtime.list_profiles() == {"providers": []}


def test_real_connection_cli_is_disabled_unless_explicitly_enabled(monkeypatch, capsys):
    from shuliu_runtime.models.connection import connection_test_cli

    monkeypatch.delenv("SHULIU_MODEL_CONNECTION_TEST", raising=False)

    assert connection_test_cli(["--provider", "provider-a", "--capability", "fast_chat"]) == 2
    assert json.loads(capsys.readouterr().out)["safe_error_code"] == "MODEL_CONNECTION_TEST_DISABLED"


def test_connection_test_budget_limits_repeated_canaries_per_runtime_process():
    from shuliu_runtime.models.connection import _ConnectionTestBudget

    budget = _ConnectionTestBudget(max_probes=2)

    assert budget.reserve("org-a", "budget-a", "provider-a", "fast_chat", "trace-1", call_id="call-1")
    assert not budget.reserve("org-a", "budget-a", "provider-a", "fast_chat", "trace-concurrent", call_id="call-concurrent")
    budget.release("org-a", "budget-a", "call-1")
    assert budget.reserve("org-a", "budget-a", "provider-a", "fast_chat", "trace-2", call_id="call-2")
    budget.settle("org-a", "budget-a", "call-2", {"total_tokens": 2}, 0.001)
    assert not budget.reserve("org-a", "budget-a", "provider-a", "fast_chat", "trace-3", call_id="call-3")


def test_connection_canary_limits_completion_output_tokens():
    from shuliu_runtime.models.providers.litellm_adapter import LiteLLMAdapter
    from shuliu_runtime.models.gateway import ModelRequest

    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return {"choices": [{"message": {"content": "OK"}}]}

    profile = _profile()
    adapter = LiteLLMAdapter(completion=completion, max_output_tokens=8)
    request = ModelRequest(
        capability="fast_chat",
        privacy="PUBLIC",
        budget_ref="budget://org-a/test",
        input="Reply with exactly OK.",
        trace_id="trace-limited-canary",
        organization_id="org-a",
        subject_id="user-a",
        permissions=("foundation.model.connection_test",),
    )

    adapter.invoke(profile.to_model_provider_config(), request, api_key="sk-test-marker")

    assert calls[0]["max_tokens"] == 8


def test_connection_probe_uses_the_exact_authorized_canary_text(monkeypatch):
    from shuliu_runtime.models.gateway import ModelResult

    service, _, _ = _service()
    requests = []

    def complete_for_provider(provider_id, request):
        requests.append((provider_id, request))
        return ModelResult(
            output="OK",
            requested_capability="fast_chat",
            resolved_provider="provider-a",
            resolved_model="openai/gpt-4.1-mini",
            model_version="test-model-v1",
            usage={"total_tokens": 2},
            latency_ms=1.0,
            cost=None,
        )

    monkeypatch.setattr(service._gateway, "complete_for_provider", complete_for_provider)

    result = service.test_provider_connection("provider-a", "fast_chat")

    assert result.status == "connected"
    assert len(requests) == 1
    assert requests[0][0] == "provider-a"
    assert requests[0][1].input == "Reply with exactly OK."
    assert requests[0][1].privacy == "PUBLIC"


def test_connection_results_classify_pre_send_blocks_and_provider_http_failures():
    disabled, _, _ = _service(canary_enabled=False)
    denied, _, _ = _service(policy_allowed=False)
    exhausted, _, _ = _service(budget_allowed=False)

    disabled_result = disabled.test_provider_connection("provider-a", "fast_chat")
    denied_result = denied.test_provider_connection("provider-a", "fast_chat")
    exhausted_result = exhausted.test_provider_connection("provider-a", "fast_chat")

    assert (disabled_result.request_status, disabled_result.failure_stage, disabled_result.http_status) == (
        "not_sent", "canary_gate", None,
    )
    assert (denied_result.request_status, denied_result.failure_stage, denied_result.http_status) == (
        "not_sent", "policy", None,
    )
    assert (exhausted_result.request_status, exhausted_result.failure_stage, exhausted_result.http_status) == (
        "not_sent", "budget", None,
    )


def test_connection_result_preserves_http_evidence_from_gateway():
    from shuliu_runtime.models.gateway import ModelProviderError

    service, adapter, _ = _service()

    def unauthorized(_provider, _request, api_key=None):
        assert api_key == "sk-connection-test-marker"
        raise ModelProviderError(
            "MODEL_CREDENTIAL_REJECTED",
            request_status="http_response_received",
            failure_stage="provider_http",
            http_status=401,
        )

    adapter.invoke = unauthorized
    result = service.test_provider_connection("provider-a", "fast_chat")

    assert result.safe_error_code == "MODEL_CREDENTIAL_REJECTED"
    assert result.request_status == "http_response_received"
    assert result.failure_stage == "provider_http"
    assert result.http_status == 401


def test_explicit_canary_authorization_is_consumed_after_one_attempt(monkeypatch):
    from shuliu_runtime.models.gateway import ModelResult

    service, _, _ = _service()
    calls = []

    def complete_for_provider(provider_id, request):
        calls.append((provider_id, request))
        return ModelResult(
            output="OK",
            requested_capability="fast_chat",
            resolved_provider="provider-a",
            resolved_model="openai/gpt-4.1-mini",
            model_version="test-model-v1",
            usage={"total_tokens": 2},
            latency_ms=1.0,
            cost=None,
        )

    monkeypatch.setattr(service._gateway, "complete_for_provider", complete_for_provider)

    first = service.test_provider_connection("provider-a", "fast_chat")
    replay = service.test_provider_connection("provider-a", "fast_chat")

    assert first.status == "connected"
    assert replay.status == "blocked"
    assert replay.safe_error_code == "MODEL_CONNECTION_TEST_DISABLED"
    assert replay.request_status == "not_sent"
    assert replay.failure_stage == "canary_gate"
    assert len(calls) == 1


def test_explicit_canary_authorization_allows_only_one_concurrent_gateway_call(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock
    from shuliu_runtime.models.gateway import ModelResult

    service, _, _ = _service()
    start = Barrier(3)
    calls = []
    calls_lock = Lock()

    def complete_for_provider(provider_id, request):
        with calls_lock:
            calls.append((provider_id, request))
        return ModelResult(
            output="OK",
            requested_capability="fast_chat",
            resolved_provider="provider-a",
            resolved_model="openai/gpt-4.1-mini",
            model_version="test-model-v1",
            usage={"total_tokens": 2},
            latency_ms=1.0,
            cost=None,
        )

    monkeypatch.setattr(service._gateway, "complete_for_provider", complete_for_provider)

    def test_once():
        start.wait(timeout=2)
        return service.test_provider_connection("provider-a", "fast_chat")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(test_once) for _ in range(2)]
        start.wait(timeout=2)
        results = [future.result(timeout=3) for future in futures]

    assert len(calls) == 1
    assert sorted(result.status for result in results) == ["blocked", "connected"]
