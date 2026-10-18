from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any, Callable

from ..capability import ModelCapability, ModelProviderConfig, parse_capability
from ..gateway import ModelProviderError, ModelRequest, ProviderResult


class LiteLLMAdapter:
    """Thin LiteLLM SDK adapter; it never performs provider selection or fallback."""

    def __init__(
        self,
        *,
        completion: Callable[..., object] | None = None,
        embedding: Callable[..., object] | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
        ):
            raise ValueError("model provider timeout must be positive")
        self._completion = completion
        self._embedding = embedding
        self._timeout_seconds = timeout_seconds

    def invoke(
        self,
        provider: ModelProviderConfig,
        request: ModelRequest,
        api_key: str | None = None,
    ) -> ProviderResult:
        capability = parse_capability(request.capability)
        if not provider.is_local and not api_key:
            raise ModelProviderError("MODEL_CREDENTIAL_UNAVAILABLE")
        try:
            if capability is ModelCapability.EMBEDDING:
                response = self._embedding_call()(
                    model=provider.model,
                    input=request.input,
                    api_key=api_key,
                    timeout=self._timeout_seconds,
                    num_retries=0,
                )
                output = _embedding_output(response)
            else:
                response = self._completion_call()(
                    model=provider.model,
                    messages=_messages(request.input),
                    api_key=api_key,
                    timeout=self._timeout_seconds,
                    num_retries=0,
                )
                output = _completion_output(response)
        except ModelProviderError:
            raise
        except Exception as error:
            raise ModelProviderError(_provider_error_code(error)) from None
        return ProviderResult(
            output=output,
            usage=_usage(response),
            cost=_response_cost(response),
            model_version=_text(_get(response, "model")),
        )

    def _completion_call(self) -> Callable[..., object]:
        if self._completion is not None:
            return self._completion
        try:
            from litellm import completion
        except ImportError:
            raise ModelProviderError("MODEL_PROVIDER_UNAVAILABLE") from None
        return completion

    def _embedding_call(self) -> Callable[..., object]:
        if self._embedding is not None:
            return self._embedding
        try:
            from litellm import embedding
        except ImportError:
            raise ModelProviderError("MODEL_PROVIDER_UNAVAILABLE") from None
        return embedding


def _messages(value: object) -> list[dict[str, object]]:
    if isinstance(value, str):
        return [{"role": "user", "content": value}]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        messages: list[dict[str, object]] = []
        for item in value:
            if not isinstance(item, Mapping) or not _text(item.get("role")):
                raise ModelProviderError("MODEL_INPUT_INVALID")
            messages.append(dict(item))
        if messages:
            return messages
    raise ModelProviderError("MODEL_INPUT_INVALID")


def _completion_output(response: object) -> object:
    choices = _get(response, "choices")
    if not isinstance(choices, Sequence) or not choices:
        raise ModelProviderError("MODEL_PROVIDER_RESPONSE_INVALID")
    message = _get(choices[0], "message")
    content = _get(message, "content")
    if content is None:
        raise ModelProviderError("MODEL_PROVIDER_RESPONSE_INVALID")
    return content


def _embedding_output(response: object) -> list[object]:
    data = _get(response, "data")
    if not isinstance(data, Sequence):
        raise ModelProviderError("MODEL_PROVIDER_RESPONSE_INVALID")
    embeddings = [_get(item, "embedding") for item in data]
    if not embeddings or any(value is None for value in embeddings):
        raise ModelProviderError("MODEL_PROVIDER_RESPONSE_INVALID")
    return embeddings


def _usage(response: object) -> dict[str, int]:
    usage = _get(response, "usage")
    if usage is None:
        return {}
    result: dict[str, int] = {}
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = _get(usage, field)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[field] = value
    return result


def _response_cost(response: object) -> float | None:
    hidden = _get(response, "_hidden_params")
    value = _get(hidden, "response_cost")
    if isinstance(value, bool) or not isinstance(value, (float, int)) or value < 0:
        return None
    return float(value)


def _provider_error_code(error: Exception) -> str:
    name = type(error).__name__.lower()
    if "ratelimit" in name:
        return "MODEL_RATE_LIMITED"
    if "timeout" in name:
        return "MODEL_TIMEOUT"
    if "auth" in name or "credential" in name:
        return "MODEL_CREDENTIAL_REJECTED"
    return "MODEL_PROVIDER_ERROR"


def _get(value: object, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
