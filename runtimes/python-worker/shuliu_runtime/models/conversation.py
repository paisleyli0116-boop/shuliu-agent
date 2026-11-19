from __future__ import annotations

import re
import threading
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from .capability import ModelProviderConfig
from .config import ProviderProfile
from .gateway import ModelGateway, ModelGatewayError, ModelResult


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}\Z")
_SAFE_MODEL_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_MAX_MESSAGES = 20
_MAX_MESSAGE_CHARS = 4_000
_MAX_TOTAL_CHARS = 16_000
_MAX_RESPONSE_CHARS = 32_000
_MAX_CACHED_REQUESTS = 128
_MAX_REQUESTS_PER_SIDECAR = 20
_BLOCKED_GATEWAY_CODES = frozenset(
    {
        "MODEL_BUDGET_EXHAUSTED",
        "MODEL_BUDGET_UNAVAILABLE",
        "MODEL_CAPABILITY_UNSUPPORTED",
        "MODEL_CREDENTIAL_UNAVAILABLE",
        "MODEL_NO_ELIGIBLE_PROVIDER",
        "MODEL_POLICY_DENIED",
        "MODEL_POLICY_UNAVAILABLE",
        "MODEL_PROVIDER_NOT_CONFIGURED",
        "MODEL_REQUEST_INVALID",
        "MODEL_ROUTE_MISMATCH",
    }
)


@dataclass(frozen=True)
class ConversationContext:
    """Authorization facts created by the Sidecar, never by the Renderer."""

    organization_id: str
    subject_id: str
    permissions: tuple[str, ...]
    budget_ref: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.organization_id, self.subject_id, self.budget_ref)
        ):
            raise ValueError("conversation authorization context is invalid")
        if (
            not isinstance(self.permissions, tuple)
            or not self.permissions
            or any(not isinstance(value, str) or not value.strip() for value in self.permissions)
        ):
            raise ValueError("conversation authorization context is invalid")


class LocalConversationBudget:
    """A bounded per-Sidecar request allowance, not a durable monetary ceiling."""

    def __init__(self, max_requests: int = _MAX_REQUESTS_PER_SIDECAR):
        if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests < 1:
            raise ValueError("conversation request allowance is invalid")
        self._max_requests = max_requests
        self._used: set[str] = set()
        self._active: set[str] = set()
        self._lock = threading.Lock()

    def reserve(self, organization_id, budget_ref, provider_id, capability, trace_id, *, call_id) -> bool:
        del organization_id, budget_ref, provider_id, capability, trace_id
        with self._lock:
            if call_id in self._used or self._active or len(self._used) >= self._max_requests:
                return False
            self._active.add(call_id)
            self._used.add(call_id)
            return True

    def settle(self, organization_id, budget_ref, call_id, usage, cost) -> None:
        del organization_id, budget_ref, usage, cost
        with self._lock:
            self._active.discard(call_id)

    def release(self, organization_id, budget_ref, call_id) -> None:
        del organization_id, budget_ref
        with self._lock:
            self._active.discard(call_id)
            self._used.discard(call_id)

    def mark_unknown(self, organization_id, budget_ref, call_id) -> None:
        del organization_id, budget_ref
        with self._lock:
            self._active.discard(call_id)
            self._used.add(call_id)


class ConversationService:
    """Complete a user-submitted text turn through a pinned Model Gateway route."""

    def __init__(
        self,
        profiles: tuple[ProviderProfile, ...],
        gateway: ModelGateway,
        context: ConversationContext,
    ) -> None:
        if not isinstance(profiles, tuple) or any(not isinstance(profile, ProviderProfile) for profile in profiles):
            raise TypeError("conversation provider profiles are invalid")
        if not isinstance(gateway, ModelGateway):
            raise TypeError("a Model Gateway is required")
        if not isinstance(context, ConversationContext):
            raise TypeError("trusted conversation context is required")
        if len({profile.provider_id for profile in profiles}) != len(profiles):
            raise ValueError("conversation provider IDs must be unique")
        self._profiles = {profile.provider_id: profile for profile in profiles}
        self._gateway = gateway
        self._context = context
        self._lock = threading.Lock()
        self._in_flight: set[str] = set()
        self._completed: OrderedDict[str, dict[str, object]] = OrderedDict()

    def complete(
        self,
        provider_id: object,
        model_id: object,
        messages: object,
        request_id: object,
        trace_id: object,
        external_processing_confirmed: object,
    ) -> dict[str, object]:
        safe_trace_id = _uuid_or_new(trace_id)
        safe_provider = provider_id if isinstance(provider_id, str) and _SAFE_IDENTIFIER.fullmatch(provider_id) else "unknown"
        safe_model = model_id if isinstance(model_id, str) and _SAFE_MODEL_IDENTIFIER.fullmatch(model_id) else "unknown"
        if not isinstance(request_id, str):
            return _result(
                "blocked", safe_provider, safe_model, safe_trace_id,
                "CHAT_REQUEST_INVALID", "not_sent", "preflight",
            )
        try:
            normalized_request_id = str(UUID(request_id))
        except (ValueError, TypeError, AttributeError):
            return _result(
                "blocked", safe_provider, safe_model, safe_trace_id,
                "CHAT_REQUEST_INVALID", "not_sent", "preflight",
            )
        if external_processing_confirmed is not True:
            return _result(
                "blocked", safe_provider, safe_model, safe_trace_id,
                "CHAT_EXTERNAL_PROCESSING_NOT_CONFIRMED", "not_sent", "preflight",
            )
        if not isinstance(provider_id, str) or not isinstance(model_id, str) or not _valid_messages(messages):
            return _result("blocked", safe_provider, safe_model, safe_trace_id, "CHAT_REQUEST_INVALID", "not_sent", "preflight")

        profile = self._profiles.get(provider_id)
        if (
            profile is None
            or not profile.enabled
            or profile.model != model_id
            or "fast_chat" not in profile.capabilities
        ):
            return _result("blocked", safe_provider, safe_model, safe_trace_id, "MODEL_PROVIDER_NOT_CONFIGURED", "not_sent", "provider_profile")

        with self._lock:
            cached = self._completed.get(normalized_request_id)
            if cached is not None:
                self._completed.move_to_end(normalized_request_id)
                return deepcopy(cached)
            if normalized_request_id in self._in_flight:
                return _result("blocked", profile.provider_id, profile.model, safe_trace_id, "CHAT_REQUEST_IN_PROGRESS", "unknown", "gateway")
            self._in_flight.add(normalized_request_id)

        try:
            result = self._invoke(profile, messages, normalized_request_id, safe_trace_id)
        except Exception:
            result = _result("failed", profile.provider_id, profile.model, safe_trace_id, "MODEL_CHAT_FAILED", "unknown", "unknown")
        finally:
            with self._lock:
                self._in_flight.discard(normalized_request_id)

        with self._lock:
            self._completed[normalized_request_id] = deepcopy(result)
            self._completed.move_to_end(normalized_request_id)
            while len(self._completed) > _MAX_CACHED_REQUESTS:
                self._completed.popitem(last=False)
        return result

    def _invoke(
        self,
        profile: ProviderProfile,
        messages: list[dict[str, str]],
        request_id: str,
        trace_id: str,
    ) -> dict[str, object]:
        from .gateway import ModelRequest

        request = ModelRequest(
            capability="fast_chat",
            privacy="PUBLIC",
            budget_ref=self._context.budget_ref,
            input=messages,
            trace_id=trace_id,
            organization_id=self._context.organization_id,
            subject_id=self._context.subject_id,
            permissions=self._context.permissions,
            call_id=request_id,
        )
        try:
            response = self._gateway.complete_for_provider(profile.provider_id, request, model=profile.model)
        except ModelGatewayError as error:
            safe_code = error.code
            status: Literal["blocked", "failed"] = "blocked" if safe_code in _BLOCKED_GATEWAY_CODES else "failed"
            return _result(
                status,
                profile.provider_id,
                profile.model,
                trace_id,
                safe_code,
                error.request_status,
                error.failure_stage,
                http_status=error.http_status,
            )
        if (
            not isinstance(response, ModelResult)
            or response.resolved_provider != profile.provider_id
            or response.resolved_model != profile.model
            or not isinstance(response.output, str)
            or not response.output.strip()
            or len(response.output) > _MAX_RESPONSE_CHARS
        ):
            return _result(
                "failed", profile.provider_id, profile.model, trace_id,
                "MODEL_PROVIDER_RESPONSE_INVALID", "http_response_received", "provider_response",
            )
        return _result(
            "succeeded",
            profile.provider_id,
            profile.model,
            trace_id,
            None,
            "http_response_received",
            "none",
            response=response.output,
            usage=response.usage,
            cost=response.cost,
            latency_ms=response.latency_ms,
        )


def create_conversation_ipc_handlers(runtime):
    """Expose one bounded chat operation; authorization facts stay server-side."""
    from shuliu_runtime.ipc.server import IpcRequestCancelled

    keys = {
        "provider_id",
        "model_id",
        "messages",
        "request_id",
        "trace_id",
        "external_processing_confirmed",
    }

    def complete(params, cancellation):
        if cancellation.is_set():
            raise IpcRequestCancelled()
        if not isinstance(params, dict) or set(params) != keys:
            raise ValueError("chat request is invalid")
        result = runtime.complete_chat(
            provider_id=params["provider_id"],
            model_id=params["model_id"],
            messages=params["messages"],
            request_id=params["request_id"],
            trace_id=params["trace_id"],
            external_processing_confirmed=params["external_processing_confirmed"],
        )
        if cancellation.is_set():
            # A request may already have reached a Provider. The result must not
            # be replayed; the caller receives only an unknown completion state.
            result_provider = result.get("provider_id") if isinstance(result, dict) else None
            result_model = result.get("model") if isinstance(result, dict) else None
            result_trace = result.get("trace_id") if isinstance(result, dict) else None
            return _result(
                "failed",
                result_provider if isinstance(result_provider, str) and _SAFE_IDENTIFIER.fullmatch(result_provider) else "unknown",
                result_model if isinstance(result_model, str) and _SAFE_MODEL_IDENTIFIER.fullmatch(result_model) else "unknown",
                _uuid_or_new(result_trace),
                "CHAT_COMPLETION_UNKNOWN",
                "unknown",
                "sidecar_ipc",
            )
        return result

    return {"chat.complete": complete}


def _uuid_or_new(value: object) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        return str(uuid4())


def _valid_messages(messages: object) -> bool:
    if not isinstance(messages, list) or not 1 <= len(messages) <= _MAX_MESSAGES:
        return False
    total = 0
    for index, message in enumerate(messages):
        expected_role = "user" if index % 2 == 0 else "assistant"
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message.get("role") != expected_role
            or not isinstance(message.get("content"), str)
            or not message["content"].strip()
            or len(message["content"]) > _MAX_MESSAGE_CHARS
        ):
            return False
        total += len(message["content"])
        if total > _MAX_TOTAL_CHARS:
            return False
    return messages[-1]["role"] == "user"


def _result(
    status: str,
    provider_id: str,
    model: str,
    trace_id: str,
    safe_error_code: str | None,
    request_status: str,
    failure_stage: str,
    *,
    http_status: int | None = None,
    response: str | None = None,
    usage: dict[str, int] | None = None,
    cost: float | None = None,
    latency_ms: float = 0.0,
) -> dict[str, object]:
    return {
        "status": status,
        "provider_id": provider_id,
        "model": model,
        "trace_id": trace_id,
        "safe_error_code": safe_error_code,
        "request_status": request_status if request_status in {"not_sent", "http_response_received", "unknown"} else "unknown",
        "failure_stage": failure_stage,
        "http_status": http_status if isinstance(http_status, int) and not isinstance(http_status, bool) else None,
        "response": response,
        "usage": dict(usage or {}),
        "cost": cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
        "latency_ms": latency_ms if isinstance(latency_ms, (int, float)) and not isinstance(latency_ms, bool) else 0.0,
    }
