from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Protocol

from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.rules import PolicyDecision, PolicyOutcome
from shuliu_runtime.security.secret_store import SecretResolver

from .capability import ModelCapability, ModelProviderConfig, parse_capability
from .routing import LOCAL_ONLY, ModelBudget, ModelHealth, route_candidates

_SAFE_MODEL_ERROR_CODES = frozenset(
    {
        "MODEL_BALANCE_INSUFFICIENT",
        "MODEL_BUDGET_EXHAUSTED",
        "MODEL_BUDGET_SETTLEMENT_FAILED",
        "MODEL_BUDGET_UNAVAILABLE",
        "MODEL_CAPABILITY_UNSUPPORTED",
        "MODEL_CONNECTION_FAILED",
        "MODEL_CONNECTION_TEST_DISABLED",
        "MODEL_CREDENTIAL_REJECTED",
        "MODEL_CREDENTIAL_UNAVAILABLE",
        "MODEL_INPUT_INVALID",
        "MODEL_NETWORK_ERROR",
        "MODEL_NO_ELIGIBLE_PROVIDER",
        "MODEL_NOT_FOUND",
        "MODEL_POLICY_DENIED",
        "MODEL_POLICY_UNAVAILABLE",
        "MODEL_PROVIDER_ERROR",
        "MODEL_PROVIDER_NOT_CONFIGURED",
        "MODEL_PROVIDER_RESPONSE_INVALID",
        "MODEL_PROVIDER_SERVER_ERROR",
        "MODEL_PROVIDER_UNAVAILABLE",
        "MODEL_RATE_LIMITED",
        "MODEL_REQUEST_INVALID",
        "MODEL_REQUEST_REJECTED",
        "MODEL_ROUTE_MISMATCH",
        "MODEL_TELEMETRY_UNAVAILABLE",
        "MODEL_TIMEOUT",
    }
)


_REQUEST_STATUSES = frozenset({"not_sent", "http_response_received", "unknown"})
_FAILURE_STAGES = frozenset({
    "preflight", "canary_gate", "provider_profile", "secret_resolution", "gateway", "policy",
    "budget", "provider_setup", "provider_transport", "provider_http", "provider_response", "sidecar_ipc", "unknown",
})


def _safe_request_status(value: object, default: str) -> str:
    return value if isinstance(value, str) and value in _REQUEST_STATUSES else default


def _safe_failure_stage(value: object, default: str) -> str:
    return value if isinstance(value, str) and value in _FAILURE_STAGES else default


def _safe_http_status(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599 else None


class ModelGatewayError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        request_status: str = "not_sent",
        failure_stage: str = "gateway",
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = _safe_error_code(code)
        self.request_status = _safe_request_status(request_status, "unknown")
        self.failure_stage = _safe_failure_stage(failure_stage, "unknown")
        self.http_status = _safe_http_status(http_status)


class ModelProviderError(RuntimeError):
    def __init__(
        self,
        code: str = "MODEL_PROVIDER_ERROR",
        *,
        request_status: str = "unknown",
        failure_stage: str = "unknown",
        http_status: int | None = None,
    ) -> None:
        super().__init__("model provider request failed")
        self.code = _safe_error_code(code)
        self.request_status = _safe_request_status(request_status, "unknown")
        self.failure_stage = _safe_failure_stage(failure_stage, "unknown")
        self.http_status = _safe_http_status(http_status)


@dataclass(frozen=True)
class ModelRequest:
    capability: str
    privacy: str
    budget_ref: str
    input: object
    trace_id: str
    organization_id: str
    subject_id: str
    permissions: tuple[str, ...]

    @classmethod
    def parse(cls, request: ModelRequest | Mapping[str, object]) -> ModelRequest:
        if isinstance(request, cls):
            parsed = request
        elif isinstance(request, Mapping):
            try:
                raw_permissions = request["permissions"]
                if not isinstance(raw_permissions, (tuple, list, set, frozenset)):
                    raise TypeError("permissions must be a collection")
                parsed = cls(
                    capability=request["capability"],
                    privacy=request["privacy"],
                    budget_ref=request["budget_ref"],
                    input=request["input"],
                    trace_id=request["trace_id"],
                    organization_id=request["organization_id"],
                    subject_id=request["subject_id"],
                    permissions=tuple(raw_permissions),
                )
            except (KeyError, TypeError):
                raise ModelGatewayError("MODEL_REQUEST_INVALID", "model request is invalid") from None
        else:
            raise ModelGatewayError("MODEL_REQUEST_INVALID", "model request is invalid")

        for field_name in (
            "privacy",
            "budget_ref",
            "trace_id",
            "organization_id",
            "subject_id",
        ):
            if not _non_empty(getattr(parsed, field_name)):
                raise ModelGatewayError("MODEL_REQUEST_INVALID", "model request is invalid")
        if not isinstance(parsed.input, (str, Mapping, Sequence)) or isinstance(
            parsed.input, (bytes, bytearray)
        ):
            raise ModelGatewayError("MODEL_REQUEST_INVALID", "model input shape is invalid")
        if not isinstance(parsed.permissions, tuple) or any(
            not _non_empty(permission) for permission in parsed.permissions
        ):
            raise ModelGatewayError("MODEL_REQUEST_INVALID", "model authorization context is invalid")
        try:
            parse_capability(parsed.capability)
        except ValueError as error:
            raise ModelGatewayError("MODEL_CAPABILITY_UNSUPPORTED", "model capability is unsupported") from error
        return parsed


@dataclass(frozen=True)
class ProviderResult:
    output: object
    usage: Mapping[str, int]
    cost: float | None = None
    model_version: str | None = None


@dataclass(frozen=True)
class ModelResult:
    output: object
    requested_capability: str
    resolved_provider: str
    resolved_model: str
    model_version: str
    usage: dict[str, int]
    latency_ms: float
    cost: float | None
    error_code: str | None = None


@dataclass(frozen=True)
class ModelCall:
    organization_id: str
    trace_id: str
    requested_capability: str
    resolved_provider: str
    resolved_model: str
    model_version: str
    latency_ms: float
    usage: dict[str, int]
    cost: float | None
    error_code: str | None


class ModelAdapter(Protocol):
    def invoke(
        self,
        provider: ModelProviderConfig,
        request: ModelRequest,
        api_key: str | None = None,
    ) -> ProviderResult: ...


class ModelCallSink(Protocol):
    def record(self, call: ModelCall) -> None: ...


class ModelGateway:
    """Policy-first model routing; provider SDKs never choose fallback routes."""

    def __init__(
        self,
        *,
        providers: Sequence[ModelProviderConfig],
        adapter: ModelAdapter,
        policy: PolicyEngine,
        budget: ModelBudget,
        health: ModelHealth,
        model_calls: ModelCallSink,
        secret_resolver: SecretResolver | None = None,
        fallback_enabled: bool = False,
        fallback_backoff_seconds: float = 0.1,
        max_fallback_backoff_seconds: float = 2.0,
        sleeper=time.sleep,
        monotonic=time.monotonic,
    ) -> None:
        if budget is None or health is None or policy is None or model_calls is None:
            raise TypeError("policy, budget, provider health, and model call sink are required")
        if not isinstance(fallback_enabled, bool):
            raise TypeError("fallback policy must be explicit")
        if (
            not _finite_non_negative(fallback_backoff_seconds)
            or not _finite_non_negative(max_fallback_backoff_seconds)
        ):
            raise ValueError("model fallback backoff is invalid")
        self._providers = tuple(providers)
        if any(not isinstance(provider, ModelProviderConfig) for provider in self._providers):
            raise TypeError("model provider configuration is invalid")
        provider_ids = [provider.provider_id for provider in self._providers]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("model provider IDs must be unique")
        self._adapter = adapter
        self._policy = policy
        self._budget = budget
        self._health = health
        self._model_calls = model_calls
        self._secret_resolver = secret_resolver
        self._fallback_enabled = fallback_enabled
        self._fallback_backoff_seconds = fallback_backoff_seconds
        self._max_fallback_backoff_seconds = max_fallback_backoff_seconds
        self._sleeper = sleeper
        self._monotonic = monotonic

    def complete(self, request: ModelRequest | Mapping[str, object]) -> ModelResult:
        return self._complete(request)

    def complete_for_provider(
        self, provider_id: str, request: ModelRequest | Mapping[str, object]
    ) -> ModelResult:
        if not _non_empty(provider_id):
            raise ModelGatewayError("MODEL_PROVIDER_NOT_CONFIGURED", "model provider is not configured")
        if not any(provider.provider_id == provider_id for provider in self._providers):
            raise ModelGatewayError("MODEL_PROVIDER_NOT_CONFIGURED", "model provider is not configured")
        return self._complete(request, only_provider=provider_id)

    def _complete(
        self,
        request: ModelRequest | Mapping[str, object],
        *,
        only_provider: str | None = None,
    ) -> ModelResult:
        parsed = ModelRequest.parse(request)
        capability = parse_capability(parsed.capability)
        matching = tuple(
            provider
            for provider in self._providers
            if capability.value in provider.capabilities
            and (only_provider is None or provider.provider_id == only_provider)
        )
        if not matching:
            raise ModelGatewayError(
                "MODEL_CAPABILITY_UNSUPPORTED", "no provider supports the requested capability"
            )
        candidates = route_candidates(
            matching,
            capability=capability,
            privacy=parsed.privacy,
            health=self._health,
        )
        if not candidates:
            raise ModelGatewayError(
                "MODEL_NO_ELIGIBLE_PROVIDER", "no healthy provider satisfies the request"
            )

        last_route_error: str | None = None
        last_request_status = "not_sent"
        last_failure_stage = "gateway"
        last_http_status: int | None = None
        attempted = 0
        for provider in candidates:
            decision = self._evaluate_policy(parsed, capability, provider)
            if decision.decision is not PolicyOutcome.ALLOW:
                last_route_error = "MODEL_POLICY_DENIED"
                last_request_status = "not_sent"
                last_failure_stage = "policy"
                last_http_status = None
                if not self._fallback_enabled:
                    break
                continue

            try:
                reserved = self._budget.reserve(
                    parsed.organization_id,
                    parsed.budget_ref,
                    provider.provider_id,
                    capability.value,
                    parsed.trace_id,
                )
            except Exception as error:
                raise ModelGatewayError(
                    "MODEL_BUDGET_UNAVAILABLE", "model budget could not be verified",
                    request_status="not_sent", failure_stage="budget",
                ) from None
            if not reserved:
                last_route_error = "MODEL_BUDGET_EXHAUSTED"
                last_request_status = "not_sent"
                last_failure_stage = "budget"
                last_http_status = None
                if not self._fallback_enabled:
                    break
                continue

            attempted += 1
            started = self._monotonic()
            provider_result: ProviderResult | None = None
            error_code: str | None = None
            try:
                provider_result = self._invoke_with_resolved_key(provider, parsed)
                provider_result = _validate_provider_result(provider_result)
            except Exception as error:
                if isinstance(error, ModelProviderError):
                    error_code = error.code
                    last_request_status = error.request_status
                    last_failure_stage = error.failure_stage
                    last_http_status = error.http_status
                else:
                    error_code = "MODEL_PROVIDER_ERROR"
                    last_request_status = "unknown"
                    last_failure_stage = "unknown"
                    last_http_status = None
                try:
                    self._budget.release(
                        parsed.organization_id,
                        parsed.budget_ref,
                        provider.provider_id,
                        parsed.trace_id,
                    )
                except Exception as release_error:
                    raise ModelGatewayError(
                        "MODEL_BUDGET_UNAVAILABLE", "model budget reservation could not be released",
                        request_status=last_request_status, failure_stage="budget", http_status=last_http_status,
                    ) from None
                elapsed = _elapsed_ms(started, self._monotonic())
                self._record_call(
                    parsed,
                    provider,
                    elapsed,
                    {},
                    None,
                    error_code,
                )
                last_route_error = error_code
                if not self._fallback_enabled:
                    break
                if attempted < len(candidates):
                    delay = min(self._fallback_backoff_seconds, self._max_fallback_backoff_seconds)
                    for _ in range(min(attempted - 1, 64)):
                        delay = min(delay * 2, self._max_fallback_backoff_seconds)
                    if delay:
                        self._sleeper(delay)
                continue

            assert provider_result is not None
            elapsed = _elapsed_ms(started, self._monotonic())
            try:
                self._budget.settle(
                    parsed.organization_id,
                    parsed.budget_ref,
                    dict(provider_result.usage),
                    provider_result.cost,
                )
            except Exception as error:
                self._record_call(
                    parsed,
                    provider,
                    elapsed,
                    dict(provider_result.usage),
                    provider_result.cost,
                    "MODEL_BUDGET_SETTLEMENT_FAILED",
                )
                raise ModelGatewayError(
                    "MODEL_BUDGET_SETTLEMENT_FAILED", "model usage could not be reconciled",
                    request_status="http_response_received", failure_stage="budget",
                ) from None
            self._record_call(
                parsed,
                provider,
                elapsed,
                dict(provider_result.usage),
                provider_result.cost,
                None,
                model_version=provider_result.model_version,
            )
            return ModelResult(
                output=provider_result.output,
                requested_capability=capability.value,
                resolved_provider=provider.provider_id,
                resolved_model=provider.model,
                model_version=provider_result.model_version or provider.model,
                usage=dict(provider_result.usage),
                latency_ms=elapsed,
                cost=provider_result.cost,
            )

        raise ModelGatewayError(
            last_route_error or "MODEL_PROVIDER_UNAVAILABLE",
            "all permitted model providers failed",
            request_status=last_request_status,
            failure_stage=last_failure_stage,
            http_status=last_http_status,
        )

    def _invoke_with_resolved_key(
        self, provider: ModelProviderConfig, request: ModelRequest
    ) -> ProviderResult:
        api_key = self._resolve_key(provider)
        try:
            return self._adapter.invoke(provider, request, api_key)
        finally:
            api_key = None

    def _evaluate_policy(
        self,
        request: ModelRequest,
        capability: ModelCapability,
        provider: ModelProviderConfig,
    ) -> PolicyDecision:
        action_id = f"model.invoke.{capability.value}.{provider.provider_id}"
        try:
            decision = self._policy.evaluate(
                subject={
                    "subject_id": request.subject_id,
                    "organization_id": request.organization_id,
                    "permissions": request.permissions,
                },
                action={"action_id": action_id, "risk": "LOW"},
                resource={
                    "organization_id": request.organization_id,
                    "resource_type": "model_provider",
                    "classification": request.privacy,
                    "provider_id": provider.provider_id,
                },
                context={
                    "organization_id": request.organization_id,
                    "trace_id": request.trace_id,
                    "privacy": request.privacy,
                },
            )
        except Exception as error:
            raise ModelGatewayError(
                "MODEL_POLICY_UNAVAILABLE", "model policy could not be evaluated",
                request_status="not_sent", failure_stage="policy",
            ) from None
        if not isinstance(decision, PolicyDecision):
            raise ModelGatewayError(
                "MODEL_POLICY_UNAVAILABLE", "model policy returned an invalid decision",
                request_status="not_sent", failure_stage="policy",
            )
        return decision

    def _resolve_key(self, provider: ModelProviderConfig) -> str | None:
        if provider.secret_ref is None:
            return None
        if self._secret_resolver is None:
            raise ModelProviderError(
                "MODEL_CREDENTIAL_UNAVAILABLE", request_status="not_sent", failure_stage="secret_resolution",
            )
        try:
            return self._secret_resolver.get(provider.secret_ref)
        except Exception as error:
            raise ModelProviderError(
                "MODEL_CREDENTIAL_UNAVAILABLE", request_status="not_sent", failure_stage="secret_resolution",
            ) from None

    def _record_call(
        self,
        request: ModelRequest,
        provider: ModelProviderConfig,
        latency_ms: float,
        usage: dict[str, int],
        cost: float | None,
        error_code: str | None,
        *,
        model_version: str | None = None,
    ) -> None:
        call = ModelCall(
            organization_id=request.organization_id,
            trace_id=request.trace_id,
            requested_capability=request.capability,
            resolved_provider=provider.provider_id,
            resolved_model=provider.model,
            model_version=model_version or provider.model,
            latency_ms=latency_ms,
            usage=usage,
            cost=cost,
            error_code=error_code,
        )
        try:
            self._model_calls.record(call)
        except Exception as error:
            raise ModelGatewayError(
                "MODEL_TELEMETRY_UNAVAILABLE", "model call could not be recorded",
                request_status="http_response_received", failure_stage="gateway",
            ) from None


def _validate_provider_result(result: object) -> ProviderResult:
    if not isinstance(result, ProviderResult):
        raise ModelProviderError("MODEL_PROVIDER_RESPONSE_INVALID", request_status="http_response_received", failure_stage="provider_response")
    if not isinstance(result.usage, Mapping):
        raise ModelProviderError("MODEL_PROVIDER_RESPONSE_INVALID", request_status="http_response_received", failure_stage="provider_response")
    usage: dict[str, int] = {}
    for key, value in result.usage.items():
        if not _non_empty(key) or isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ModelProviderError("MODEL_PROVIDER_RESPONSE_INVALID", request_status="http_response_received", failure_stage="provider_response")
        usage[key] = value
    if result.cost is not None and (
        isinstance(result.cost, bool)
        or not isinstance(result.cost, (int, float))
        or not isfinite(float(result.cost))
        or result.cost < 0
    ):
        raise ModelProviderError("MODEL_PROVIDER_RESPONSE_INVALID", request_status="http_response_received", failure_stage="provider_response")
    if result.output is None:
        raise ModelProviderError("MODEL_PROVIDER_RESPONSE_INVALID", request_status="http_response_received", failure_stage="provider_response")
    return ProviderResult(
        output=result.output,
        usage=usage,
        cost=float(result.cost) if result.cost is not None else None,
        model_version=result.model_version if _non_empty(result.model_version) else None,
    )


def _elapsed_ms(started: float, finished: float) -> float:
    return round(max(0.0, (finished - started) * 1000), 3)


def _finite_non_negative(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and isfinite(float(value))
        and value >= 0
    )


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_error_code(value: object) -> str:
    if isinstance(value, str) and value in _SAFE_MODEL_ERROR_CODES:
        return value
    return "MODEL_PROVIDER_ERROR"
