from __future__ import annotations

from collections.abc import Mapping

import pytest

from shuliu_runtime.models.capability import ModelProviderConfig
from shuliu_runtime.models.gateway import ModelProviderError, ModelRequest
from shuliu_runtime.models.providers.litellm_adapter import LiteLLMAdapter


class FakeProviderHttpError(Exception):
    def __init__(self, status_code: int, code: str | None = None) -> None:
        self.status_code = status_code
        self.body: Mapping[str, object] = {
            "error": {
                "code": code,
                "message": "Authorization: Bearer sk-provider-secret-do-not-display",
            }
        }
        super().__init__("request failed with sk-provider-secret-do-not-display")


def _request() -> ModelRequest:
    return ModelRequest(
        capability="fast_chat",
        privacy="PUBLIC",
        budget_ref="budget://org/test",
        input="public canary",
        trace_id="trace-test",
        organization_id="org-test",
        subject_id="user-test",
        permissions=("foundation.model.connection_test",),
    )


def _provider(provider_id: str, model: str) -> ModelProviderConfig:
    return ModelProviderConfig(
        provider_id=provider_id,
        model=model,
        capabilities=frozenset({"fast_chat"}),
        priority=1,
        is_local=False,
    )


@pytest.mark.parametrize(
    ("provider_id", "model", "expected_base"),
    [
        ("deepseek", "deepseek/deepseek-flash", "https://api.deepseek.com"),
        ("openai-compatible", "openai/gpt-4.1-mini", None),
    ],
)
def test_completion_uses_official_base_only_for_deepseek(
    provider_id: str,
    model: str,
    expected_base: str | None,
) -> None:
    calls: list[dict[str, object]] = []

    def completion(**kwargs: object) -> object:
        calls.append(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    adapter = LiteLLMAdapter(completion=completion)
    adapter.invoke(_provider(provider_id, model), _request(), api_key="fake-secret")

    assert len(calls) == 1
    if expected_base is None:
        assert "api_base" not in calls[0]
    else:
        assert calls[0]["api_base"] == expected_base
    assert calls[0]["num_retries"] == 0


@pytest.mark.parametrize(
    ("status_code", "provider_code", "expected"),
    [
        (401, None, "MODEL_CREDENTIAL_REJECTED"),
        (403, None, "MODEL_PROVIDER_ERROR"),
        (403, "invalid_api_key", "MODEL_CREDENTIAL_REJECTED"),
        (402, None, "MODEL_BALANCE_INSUFFICIENT"),
        (429, None, "MODEL_RATE_LIMITED"),
        (408, None, "MODEL_TIMEOUT"),
        (400, None, "MODEL_REQUEST_REJECTED"),
        (422, "invalid_parameter", "MODEL_REQUEST_REJECTED"),
        (503, None, "MODEL_PROVIDER_SERVER_ERROR"),
        (404, "model_not_found", "MODEL_NOT_FOUND"),
        (404, None, "MODEL_PROVIDER_ERROR"),
    ],
)
def test_http_errors_map_to_safe_codes_without_provider_text(
    status_code: int,
    provider_code: str | None,
    expected: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = FakeProviderHttpError(status_code, provider_code)
    calls = 0

    def completion(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        assert kwargs["num_retries"] == 0
        raise error

    adapter = LiteLLMAdapter(completion=completion)
    with pytest.raises(ModelProviderError) as captured:
        adapter.invoke(
            _provider("deepseek", "deepseek/deepseek-flash"),
            _request(),
            api_key="fake-secret",
        )

    assert captured.value.code == expected
    assert str(captured.value) == "model provider request failed"
    assert "sk-provider-secret-do-not-display" not in str(captured.value)
    assert "sk-provider-secret-do-not-display" not in caplog.text
    assert captured.value.__suppress_context__ is True
    assert calls == 1


@pytest.mark.parametrize(
    ("error_type", "expected"),
    [
        ("APITimeoutError", "MODEL_TIMEOUT"),
        ("APIConnectionError", "MODEL_NETWORK_ERROR"),
        ("RateLimitError", "MODEL_RATE_LIMITED"),
    ],
)
def test_sdk_network_and_timeout_errors_are_classified_without_text(
    error_type: str,
    expected: str,
) -> None:
    error_class = type(error_type, (Exception,), {})
    error = error_class("request URL and key must not be exposed")
    calls = 0

    def completion(**_: object) -> object:
        nonlocal calls
        calls += 1
        raise error

    adapter = LiteLLMAdapter(completion=completion)
    with pytest.raises(ModelProviderError) as captured:
        adapter.invoke(
            _provider("deepseek", "deepseek/deepseek-flash"),
            _request(),
            api_key="fake-secret",
        )

    assert captured.value.code == expected
    assert "request URL and key" not in str(captured.value)
    assert calls == 1


def test_unrecognized_error_code_is_not_exposed_as_a_safe_diagnostic() -> None:
    assert ModelProviderError("INTERNAL_SECRET_MARKER").code == "MODEL_PROVIDER_ERROR"


@pytest.mark.parametrize(("status_code", "expected"), [
    (401, "MODEL_CREDENTIAL_REJECTED"),
    (402, "MODEL_BALANCE_INSUFFICIENT"),
    (429, "MODEL_RATE_LIMITED"),
    (503, "MODEL_PROVIDER_SERVER_ERROR"),
])
def test_http_failure_preserves_request_status_and_status_code(status_code, expected):
    def completion(**_):
        raise FakeProviderHttpError(status_code)

    adapter = LiteLLMAdapter(completion=completion)
    with pytest.raises(ModelProviderError) as captured:
        adapter.invoke(
            _provider("deepseek", "deepseek/deepseek-flash"),
            _request(),
            api_key="fake-secret",
        )

    assert captured.value.code == expected
    assert captured.value.request_status == "http_response_received"
    assert captured.value.failure_stage == "provider_http"
    assert captured.value.http_status == status_code


def test_network_failure_keeps_request_status_unknown_and_never_invents_http_status():
    def completion(**_):
        raise type("APIConnectionError", (Exception,), {})("secret-bearing exception")

    adapter = LiteLLMAdapter(completion=completion)
    with pytest.raises(ModelProviderError) as captured:
        adapter.invoke(
            _provider("deepseek", "deepseek/deepseek-flash"),
            _request(),
            api_key="fake-secret",
        )

    assert captured.value.code == "MODEL_NETWORK_ERROR"
    assert captured.value.request_status == "unknown"
    assert captured.value.failure_stage == "provider_transport"
    assert captured.value.http_status is None


def test_malformed_provider_response_is_marked_as_response_received():
    adapter = LiteLLMAdapter(completion=lambda **_: {"choices": []})
    with pytest.raises(ModelProviderError) as captured:
        adapter.invoke(
            _provider("deepseek", "deepseek/deepseek-flash"),
            _request(),
            api_key="fake-secret",
        )

    assert captured.value.code == "MODEL_PROVIDER_RESPONSE_INVALID"
    assert captured.value.request_status == "http_response_received"
    assert captured.value.failure_stage == "provider_response"


def test_dashscope_embedding_uses_openai_compatible_route_dimensions_and_no_retry():
    calls: list[dict[str, object]] = []

    def embedding(**kwargs: object) -> object:
        calls.append(kwargs)
        return {
            "data": [{"embedding": [0.1] * 1024}],
            "usage": {"total_tokens": 4},
            "model": "text-embedding-v3",
        }

    provider = ModelProviderConfig(
        provider_id="dashscope",
        model="text-embedding-v3",
        capabilities=frozenset({"embedding"}),
        priority=1,
        is_local=False,
        secret_ref="secret://os/providers/dashscope/credential",
        embedding_api_base="https://test-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        embedding_dimensions=1024,
    )
    request = ModelRequest(
        capability="embedding",
        privacy="PUBLIC",
        budget_ref="budget://org/test",
        input=["public test fixture"],
        trace_id="trace-dashscope-test",
        organization_id="org-test",
        subject_id="user-test",
        permissions=("foundation.model.invoke",),
        dimensions=1024,
    )

    result = LiteLLMAdapter(embedding=embedding).invoke(provider, request, api_key="fake-secret")

    assert result.output == [[0.1] * 1024]
    assert calls == [
        {
            "model": "openai/text-embedding-v3",
            "input": ["public test fixture"],
            "api_key": "fake-secret",
            "timeout": 60.0,
            "num_retries": 0,
            "api_base": provider.embedding_api_base,
            "dimensions": 1024,
        }
    ]


def test_dashscope_embedding_dimension_mismatch_fails_before_provider_call():
    calls: list[dict[str, object]] = []
    provider = ModelProviderConfig(
        provider_id="dashscope",
        model="text-embedding-v3",
        capabilities=frozenset({"embedding"}),
        priority=1,
        is_local=False,
        secret_ref="secret://os/providers/dashscope/credential",
        embedding_api_base="https://test-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        embedding_dimensions=1024,
    )
    request = ModelRequest(
        capability="embedding",
        privacy="PUBLIC",
        budget_ref="budget://org/test",
        input=["public test fixture"],
        trace_id="trace-dashscope-dimension-mismatch",
        organization_id="org-test",
        subject_id="user-test",
        permissions=("foundation.model.invoke",),
        dimensions=768,
    )

    with pytest.raises(ModelProviderError) as captured:
        LiteLLMAdapter(embedding=lambda **kwargs: calls.append(kwargs)).invoke(
            provider, request, api_key="fake-secret"
        )

    assert captured.value.code == "MODEL_REQUEST_INVALID"
    assert captured.value.request_status == "not_sent"
    assert captured.value.failure_stage == "preflight"
    assert calls == []
    assert captured.value.http_status is None


def test_dashscope_embedding_rejects_provider_vector_with_wrong_dimensions():
    provider = ModelProviderConfig(
        provider_id="dashscope",
        model="text-embedding-v3",
        capabilities=frozenset({"embedding"}),
        priority=1,
        is_local=False,
        secret_ref="secret://os/providers/dashscope/credential",
        embedding_api_base="https://test-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        embedding_dimensions=1024,
    )
    request = ModelRequest(
        capability="embedding",
        privacy="PUBLIC",
        budget_ref="budget://org/test",
        input=["public test fixture"],
        trace_id="trace-dashscope-response-dimensions",
        organization_id="org-test",
        subject_id="user-test",
        permissions=("foundation.model.invoke",),
        dimensions=1024,
    )
    adapter = LiteLLMAdapter(
        embedding=lambda **_: {
            "data": [{"embedding": [0.1] * 768}],
            "usage": {"total_tokens": 4},
            "model": "text-embedding-v3",
        }
    )

    with pytest.raises(ModelProviderError) as captured:
        adapter.invoke(provider, request, api_key="fake-secret")

    assert captured.value.code == "MODEL_PROVIDER_RESPONSE_INVALID"
    assert captured.value.request_status == "http_response_received"
    assert captured.value.failure_stage == "provider_response"


def test_litellm_log_level_defaults_to_error_for_protocol_channel_safety(monkeypatch):
    """litellm routes INFO/DEBUG logs to sys.stdout; the sidecar's stdout is the
    IPC channel. The adapter must default LITELLM_LOG to ERROR before litellm
    reads the environment at import time."""
    import importlib
    import os

    import shuliu_runtime.models.providers.litellm_adapter as adapter

    monkeypatch.delenv("LITELLM_LOG", raising=False)
    importlib.reload(adapter)

    assert os.environ.get("LITELLM_LOG") == "ERROR"
