from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any, Callable

from ..capability import ModelCapability, ModelProviderConfig, parse_capability
from ..gateway import ModelProviderError, ModelRequest, ProviderResult

_DEEPSEEK_API_BASE = "https://api.deepseek.com"
_STRUCTURED_PROVIDER_ERROR_CODES = {
    "authentication_error": "MODEL_CREDENTIAL_REJECTED",
    "invalid_api_key": "MODEL_CREDENTIAL_REJECTED",
    "insufficient_balance": "MODEL_BALANCE_INSUFFICIENT",
    "balance_insufficient": "MODEL_BALANCE_INSUFFICIENT",
    "rate_limit_exceeded": "MODEL_RATE_LIMITED",
    "rate_limit_reached": "MODEL_RATE_LIMITED",
    "invalid_parameter": "MODEL_REQUEST_REJECTED",
    "invalid_parameters": "MODEL_REQUEST_REJECTED",
    "invalid_request_error": "MODEL_REQUEST_REJECTED",
    "model_not_found": "MODEL_NOT_FOUND",
    "model_not_exist": "MODEL_NOT_FOUND",
}


class LiteLLMAdapter:
    """Thin LiteLLM SDK adapter; it never performs provider selection or fallback."""

    def __init__(
        self,
        *,
        completion: Callable[..., object] | None = None,
        embedding: Callable[..., object] | None = None,
        timeout_seconds: float = 60.0,
        max_output_tokens: int | None = None,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
        ):
            raise ValueError("model provider timeout must be positive")
        if max_output_tokens is not None and (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or max_output_tokens < 1
        ):
            raise ValueError("model provider output token limit must be positive")
        self._completion = completion
        self._embedding = embedding
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens

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
                completion_options: dict[str, object] = {
                    "model": provider.model,
                    "messages": _messages(request.input),
                    "api_key": api_key,
                    "timeout": self._timeout_seconds,
                    "num_retries": 0,
                }
                if self._max_output_tokens is not None:
                    completion_options["max_tokens"] = self._max_output_tokens
                if provider.provider_id.casefold() == "deepseek":
                    completion_options["api_base"] = _DEEPSEEK_API_BASE
                response = self._completion_call()(**completion_options)
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
    status = _http_status(error)
    structured_code = _structured_provider_error_code(error)

    if status in {401, 403}:
        return "MODEL_CREDENTIAL_REJECTED"
    if status == 402:
        return "MODEL_BALANCE_INSUFFICIENT"
    if status == 429:
        return "MODEL_RATE_LIMITED"
    if status in {408, 504}:
        return "MODEL_TIMEOUT"
    if status in {400, 422}:
        return "MODEL_REQUEST_REJECTED"
    if status == 404:
        return structured_code or "MODEL_PROVIDER_ERROR"
    if status is not None and 500 <= status <= 599:
        return "MODEL_PROVIDER_SERVER_ERROR"
    if structured_code is not None:
        return structured_code

    error_types = {base.__name__ for base in type(error).__mro__}
    if error_types.intersection({"AuthenticationError", "UnauthorizedError"}):
        return "MODEL_CREDENTIAL_REJECTED"
    if error_types.intersection({
        "APITimeoutError", "ConnectTimeout", "PoolTimeout", "ReadTimeout",
        "Timeout", "TimeoutError", "TimeoutException", "WriteTimeout",
    }):
        return "MODEL_TIMEOUT"
    if error_types.intersection({
        "APIConnectionError", "ConnectError", "ConnectionError", "ConnectionRefusedError",
        "ConnectionResetError", "NetworkError", "ProtocolError", "ProxyError",
        "RemoteProtocolError", "SSLError", "gaierror",
    }):
        return "MODEL_NETWORK_ERROR"
    if "RateLimitError" in error_types:
        return "MODEL_RATE_LIMITED"
    if error_types.intersection({"BadRequestError", "InvalidRequestError", "UnprocessableEntityError"}):
        return "MODEL_REQUEST_REJECTED"
    if error_types.intersection({"InternalServerError", "ServiceUnavailableError"}):
        return "MODEL_PROVIDER_SERVER_ERROR"
    return "MODEL_PROVIDER_ERROR"


def _http_status(error: Exception) -> int | None:
    response = _safe_attribute(error, "response")
    for source in (error, response):
        status = _safe_attribute(source, "status_code")
        if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
            return status
    return None


def _structured_provider_error_code(error: Exception) -> str | None:
    candidates = [_safe_attribute(error, "code")]
    body = _safe_attribute(error, "body")
    if isinstance(body, Mapping):
        candidates.append(body.get("code"))
        details = body.get("error")
        if isinstance(details, Mapping):
            candidates.extend((details.get("code"), details.get("type")))
    for candidate in candidates:
        if isinstance(candidate, str):
            mapped = _STRUCTURED_PROVIDER_ERROR_CODES.get(candidate.strip().casefold())
            if mapped is not None:
                return mapped
    return None


def _safe_attribute(value: object, name: str) -> object | None:
    try:
        return getattr(value, name, None)
    except Exception:
        return None


def _get(value: object, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
