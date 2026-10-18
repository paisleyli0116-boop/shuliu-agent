from __future__ import annotations

import time
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Protocol

from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.rules import PolicyDecision, PolicyOutcome
from shuliu_runtime.security.secret_store import SecretResolver

from .capability import ModelCapability, ModelProviderConfig, parse_capability
from .routing import LOCAL_ONLY, ModelBudget, ModelHealth, route_candidates


class ModelGatewayError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = _safe_error_code(code)


class ModelProviderError(RuntimeError):
    def __init__(self, code: str = "MODEL_PROVIDER_ERROR") -> None:
        super().__init__("model provider request failed")
        self.code = _safe_error_code(code)


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
        parsed = ModelRequest.parse(request)
        capability = parse_capability(parsed.capability)
        matching = tuple(
            provider for provider in self._providers if capability.value in provider.capabilities
        )
        if not matching:
            raise ModelGatewayError(
                "MODEL_CAPABILITY_UNSUPPORTED", "no provider supports the requested capability"
            )
        candidates = route_candidates(
            self._providers,
            capability=capability,
            privacy=parsed.privacy,
            health=self._health,
        )
        if not candidates:
            raise ModelGatewayError(
                "MODEL_NO_ELIGIBLE_PROVIDER", "no healthy provider satisfies the request"
            )

        last_route_error: str | None = None
        attempted = 0
        for provider in candidates:
            decision = self._evaluate_policy(parsed, capability, provider)
            if decision.decision is not PolicyOutcome.ALLOW:
                last_route_error = "MODEL_POLICY_DENIED"
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
                    "MODEL_BUDGET_UNAVAILABLE", "model budget could not be verified"
                ) from None
            if not reserved:
                last_route_error = "MODEL_BUDGET_EXHAUSTED"
                if not self._fallback_enabled:
                    break
                continue

            attempted += 1
            started = self._monotonic()
            provider_result: ProviderResult | None = None
            error_code: str | None = None
            try:
                api_key = self._resolve_key(provider)
                provider_result = self._adapter.invoke(provider, parsed, api_key)
                provider_result = _validate_provider_result(provider_result)
            except Exception as error:
                error_code = (
                    error.code
                    if isinstance(error, ModelProviderError)
                    else "MODEL_PROVIDER_ERROR"
                )
                try:
                    self._budget.release(
                        parsed.organization_id,
                        parsed.budget_ref,
                        provider.provider_id,
                        parsed.trace_id,
                    )
                except Exception as release_error:
                    raise ModelGatewayError(
                        "MODEL_BUDGET_UNAVAILABLE", "model budget reservation could not be released"
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
                    "MODEL_BUDGET_SETTLEMENT_FAILED", "model usage could not be reconciled"
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
        )

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
            raise ModelGatewayError("MODEL_POLICY_UNAVAILABLE", "model policy could not be evaluated") from None
        if not isinstance(decision, PolicyDecision):
            raise ModelGatewayError("MODEL_POLICY_UNAVAILABLE", "model policy returned an invalid decision")
        return decision

    def _resolve_key(self, provider: ModelProviderConfig) -> str | None:
        if provider.secret_ref is None:
            return None
        if self._secret_resolver is None:
            raise ModelProviderError("MODEL_CREDENTIAL_UNAVAILABLE")
        try:
            return self._secret_resolver.get(provider.secret_ref)
        except Exception as error:
            raise ModelProviderError("MODEL_CREDENTIAL_UNAVAILABLE") from None

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
                "MODEL_TELEMETRY_UNAVAILABLE", "model call could not be recorded"
            ) from None


def _validate_provider_result(result: object) -> ProviderResult:
    if not isinstance(result, ProviderResult):
        raise ModelProviderError("MODEL_PROVIDER_RESPONSE_INVALID")
    if not isinstance(result.usage, Mapping):
        raise ModelProviderError("MODEL_PROVIDER_RESPONSE_INVALID")
    usage: dict[str, int] = {}
    for key, value in result.usage.items():
        if not _non_empty(key) or isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ModelProviderError("MODEL_PROVIDER_RESPONSE_INVALID")
        usage[key] = value
    if result.cost is not None and (
        isinstance(result.cost, bool)
        or not isinstance(result.cost, (int, float))
        or not isfinite(float(result.cost))
        or result.cost < 0
    ):
        raise ModelProviderError("MODEL_PROVIDER_RESPONSE_INVALID")
    if result.output is None:
        raise ModelProviderError("MODEL_PROVIDER_RESPONSE_INVALID")
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
    if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value):
        return value
    return "MODEL_PROVIDER_ERROR"
