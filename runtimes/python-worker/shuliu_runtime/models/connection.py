from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable, Literal
from uuid import UUID, uuid4

from .capability import ModelCapability, parse_capability
from .config import ProviderConfigurationError, ProviderProfile, load_provider_profiles
from .conversation import ConversationContext, ConversationService, LocalConversationBudget
from .gateway import ModelGateway, ModelGatewayError, ModelRequest, ModelResult
from .providers.litellm_adapter import LiteLLMAdapter
from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.production.identity import ProductionIdentity
from shuliu_runtime.policy.rules import PolicyRule
from shuliu_runtime.security.os_secret_store import OSSecretStore
from shuliu_runtime.security.secret_ref import SecretRef
from shuliu_runtime.security.secret_store import SecretResolver, SecretStoreUnavailableError


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}\Z")
_SAFE_ERROR_CODE = re.compile(r"[A-Z0-9_]{1,64}\Z")
_BLOCKED_CODES = frozenset(
    {
        "MODEL_BUDGET_EXHAUSTED",
        "MODEL_BUDGET_UNAVAILABLE",
        "MODEL_CAPABILITY_UNSUPPORTED",
        "MODEL_CREDENTIAL_UNAVAILABLE",
        "MODEL_NO_ELIGIBLE_PROVIDER",
        "MODEL_POLICY_DENIED",
        "MODEL_POLICY_UNAVAILABLE",
        "MODEL_PROVIDER_NOT_CONFIGURED",
    }
)
_CANARY_INPUT = "Reply with exactly OK."
_SAFE_TRACE_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")


@dataclass(frozen=True)
class ProviderConnectionContext:
    organization_id: str
    subject_id: str
    permissions: tuple[str, ...]
    budget_ref: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.organization_id, self.subject_id, self.budget_ref)
        ):
            raise ValueError("provider connection authorization context is invalid")
        if (
            not isinstance(self.permissions, tuple)
            or not self.permissions
            or any(not isinstance(value, str) or not value.strip() for value in self.permissions)
        ):
            raise ValueError("provider connection authorization context is invalid")


@dataclass(frozen=True)
class ProviderConnectionResult:
    status: Literal["connected", "blocked", "failed"]
    provider_id: str
    model: str
    capability: str
    latency_ms: float
    trace_id: str
    safe_error_code: str | None = None
    request_status: Literal["not_sent", "http_response_received", "unknown"] = "unknown"
    failure_stage: Literal[
        "none", "preflight", "canary_gate", "provider_profile", "secret_resolution", "gateway", "policy",
        "budget", "provider_setup", "provider_transport", "provider_http", "provider_response", "sidecar_ipc", "unknown",
    ] = "unknown"
    http_status: int | None = None


class ProviderConnectionService:
    """Run a fixed, public canary through a provider-scoped Model Gateway route."""

    def __init__(
        self,
        profiles: tuple[ProviderProfile, ...],
        gateway: ModelGateway,
        context: ProviderConnectionContext,
        *,
        canary_enabled: bool = False,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(profiles, tuple) or any(
            not isinstance(profile, ProviderProfile) for profile in profiles
        ):
            raise TypeError("provider profiles are invalid")
        provider_ids = [profile.provider_id for profile in profiles]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("provider IDs must be unique")
        if not isinstance(gateway, ModelGateway):
            raise TypeError("a Model Gateway is required")
        if not isinstance(context, ProviderConnectionContext):
            raise TypeError("trusted connection authorization context is required")
        if not isinstance(canary_enabled, bool):
            raise TypeError("provider canary enablement must be explicit")
        self._profiles = {profile.provider_id: profile for profile in profiles}
        self._gateway = gateway
        self._context = context
        self._canary_enabled = canary_enabled
        self._monotonic = monotonic
        self._canary_lock = threading.Lock()
        self._canary_consumed = False

    def test_provider_connection(
        self, provider_id: str, capability: ModelCapability | str, trace_id: str | None = None
    ) -> ProviderConnectionResult:
        if trace_id is None:
            trace_id = str(uuid4())
        elif not isinstance(trace_id, str) or not _SAFE_TRACE_ID.fullmatch(trace_id):
            trace_id = str(uuid4())
            return self._result(
                "blocked", provider_id if isinstance(provider_id, str) and _SAFE_IDENTIFIER.fullmatch(provider_id) else "unknown", "",
                capability if isinstance(capability, str) and _SAFE_IDENTIFIER.fullmatch(capability) else "unknown",
                trace_id, "MODEL_REQUEST_INVALID", 0.0,
                request_status="not_sent", failure_stage="preflight",
            )
        else:
            trace_id = str(UUID(trace_id))
        safe_provider_id = (
            provider_id if isinstance(provider_id, str) and _SAFE_IDENTIFIER.fullmatch(provider_id) else "unknown"
        )
        safe_capability = capability.value if isinstance(capability, ModelCapability) else (
            capability if isinstance(capability, str) and _SAFE_IDENTIFIER.fullmatch(capability) else "unknown"
        )
        with self._canary_lock:
            canary_authorized = self._canary_enabled and not self._canary_consumed
            if canary_authorized:
                self._canary_consumed = True
        if not canary_authorized:
            return self._result(
                "blocked", safe_provider_id, "", safe_capability, trace_id,
                "MODEL_CONNECTION_TEST_DISABLED", 0.0,
                request_status="not_sent", failure_stage="canary_gate",
            )
        profile = self._profiles.get(provider_id) if isinstance(provider_id, str) else None
        if profile is None:
            return self._result(
                "blocked", safe_provider_id, "", safe_capability, trace_id,
                "MODEL_PROVIDER_NOT_CONFIGURED", 0.0,
                request_status="not_sent", failure_stage="provider_profile",
            )
        if not profile.enabled:
            return self._result(
                "blocked", profile.provider_id, profile.model, safe_capability, trace_id,
                "MODEL_PROVIDER_NOT_CONFIGURED", 0.0,
                request_status="not_sent", failure_stage="provider_profile",
            )
        try:
            parsed_capability = parse_capability(capability)
        except ValueError:
            return self._result(
                "blocked", profile.provider_id, profile.model, "unknown", trace_id,
                "MODEL_CAPABILITY_UNSUPPORTED", 0.0,
                request_status="not_sent", failure_stage="preflight",
            )
        if parsed_capability.value not in profile.capabilities:
            return self._result(
                "blocked", profile.provider_id, profile.model, parsed_capability.value, trace_id,
                "MODEL_CAPABILITY_UNSUPPORTED", 0.0,
                request_status="not_sent", failure_stage="preflight",
            )
        if not profile.is_local and not profile.secret_ref:
            return self._result(
                "blocked", profile.provider_id, profile.model, parsed_capability.value, trace_id,
                "MODEL_CREDENTIAL_UNAVAILABLE", 0.0,
                request_status="not_sent", failure_stage="secret_resolution",
            )

        request = ModelRequest(
            capability=parsed_capability.value,
            privacy="PUBLIC",
            budget_ref=self._context.budget_ref,
            input=_CANARY_INPUT,
            trace_id=trace_id,
            organization_id=self._context.organization_id,
            subject_id=self._context.subject_id,
            permissions=self._context.permissions,
        )
        started = self._monotonic()
        try:
            result = self._gateway.complete_for_provider(profile.provider_id, request)
        except ModelGatewayError as error:
            code = _safe_error_code(error.code)
            status = "blocked" if code in _BLOCKED_CODES else "failed"
            return self._result(
                status, profile.provider_id, profile.model, parsed_capability.value,
                trace_id, code, _elapsed_ms(started, self._monotonic()),
                request_status=error.request_status,
                failure_stage=error.failure_stage,
                http_status=error.http_status,
            )
        except Exception:
            return self._result(
                "failed", profile.provider_id, profile.model, parsed_capability.value,
                trace_id, "MODEL_CONNECTION_FAILED", _elapsed_ms(started, self._monotonic()),
                request_status="unknown", failure_stage="unknown",
            )

        if (
            not isinstance(result, ModelResult)
            or result.resolved_provider != profile.provider_id
            or result.resolved_model != profile.model
        ):
            return self._result(
                "failed", profile.provider_id, profile.model, parsed_capability.value,
                trace_id, "MODEL_ROUTE_MISMATCH", _elapsed_ms(started, self._monotonic()),
                request_status="http_response_received", failure_stage="provider_response",
            )
        return self._result(
            "connected", profile.provider_id, profile.model, parsed_capability.value,
            trace_id, None, result.latency_ms,
            request_status="http_response_received", failure_stage="none",
        )

    @staticmethod
    def _result(
        status: Literal["connected", "blocked", "failed"],
        provider_id: str,
        model: str,
        capability: str,
        trace_id: str,
        safe_error_code: str | None,
        latency_ms: float,
        *,
        request_status: Literal["not_sent", "http_response_received", "unknown"] = "not_sent",
        failure_stage: str = "preflight",
        http_status: int | None = None,
    ) -> ProviderConnectionResult:
        return ProviderConnectionResult(
            status=status,
            provider_id=provider_id,
            model=model,
            capability=capability,
            latency_ms=latency_ms,
            trace_id=trace_id,
            safe_error_code=_safe_error_code(safe_error_code) if safe_error_code is not None else None,
            request_status=request_status if request_status in {"not_sent", "http_response_received", "unknown"} else "unknown",
            failure_stage=failure_stage if failure_stage in {
                "none", "preflight", "canary_gate", "provider_profile", "secret_resolution", "gateway", "policy",
                "budget", "provider_setup", "provider_transport", "provider_http", "provider_response", "sidecar_ipc", "unknown",
            } else "unknown",
            http_status=http_status if isinstance(http_status, int) and not isinstance(http_status, bool) and 100 <= http_status <= 599 else None,
        )


def _safe_error_code(code: object) -> str:
    return code if isinstance(code, str) and _SAFE_ERROR_CODE.fullmatch(code) else "MODEL_CONNECTION_FAILED"


def _elapsed_ms(started: float, finished: float) -> float:
    return round(max(0.0, (finished - started) * 1000), 3)

class ProviderRuntime:
    """Fixed desktop provider operations; plaintext credentials go only to SecretResolver."""

    def __init__(self, profiles, secret_resolver, connection_service, conversation_service=None):
        if not isinstance(profiles, tuple) or any(not isinstance(item, ProviderProfile) for item in profiles):
            raise TypeError("provider profiles are invalid")
        if not callable(getattr(secret_resolver, "put", None)) or not callable(
            getattr(secret_resolver, "get", None)
        ):
            raise TypeError("SecretResolver is required")
        if not isinstance(connection_service, ProviderConnectionService):
            raise TypeError("provider connection service is required")
        if conversation_service is not None and not isinstance(conversation_service, ConversationService):
            raise TypeError("conversation service is invalid")
        self._profiles = {profile.provider_id: profile for profile in profiles}
        self._secret_resolver = secret_resolver
        self._connection_service = connection_service
        self._conversation_service = conversation_service

    def list_profiles(self) -> dict[str, object]:
        providers = []
        for profile in sorted(self._profiles.values(), key=lambda item: (item.priority, item.provider_id)):
            providers.append(
                {
                    "id": profile.provider_id,
                    "name": profile.provider_id,
                    "models": [
                        {
                            "id": profile.model,
                            "name": profile.model,
                            "capabilities": sorted(profile.capabilities),
                        }
                    ],
                    "selected_model_id": profile.model,
                    "configured": self._is_configured(profile),
                    "enabled": profile.enabled,
                }
            )
        return {"providers": providers}

    def _is_configured(self, profile: ProviderProfile) -> bool:
        if profile.secret_ref is None:
            return False
        try:
            return (
                self._secret_resolver.get(_credential_status_reference(profile))
                == "provider-configured-v1"
            )
        except Exception:
            return False

    def save_profile(self, provider_id: str, model_id: str, api_key: str) -> dict[str, object]:
        profile = self._profile_for_credential(provider_id, model_id)
        self._store_credential(profile, api_key)
        return self._credential_result(profile)

    def replace_credential(self, profile_id: str, api_key: str) -> dict[str, object]:
        profile = self._profile_for_credential(profile_id, None)
        self._store_credential(profile, api_key)
        return self._credential_result(profile)

    def test_connection(self, provider_id: str, capability: str, trace_id: str | None = None) -> dict[str, object]:
        return asdict(self._connection_service.test_provider_connection(provider_id, capability, trace_id))

    def complete_chat(
        self,
        *,
        provider_id: object,
        model_id: object,
        messages: object,
        request_id: object,
        trace_id: object,
        external_processing_confirmed: object,
    ) -> dict[str, object]:
        if self._conversation_service is None:
            return {
                "status": "blocked",
                "provider_id": provider_id if isinstance(provider_id, str) and _SAFE_IDENTIFIER.fullmatch(provider_id) else "unknown",
                "model": model_id if isinstance(model_id, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", model_id) else "unknown",
                "trace_id": trace_id if isinstance(trace_id, str) and _SAFE_TRACE_ID.fullmatch(trace_id) else str(uuid4()),
                "safe_error_code": "CHAT_SERVICE_UNAVAILABLE",
                "request_status": "not_sent",
                "failure_stage": "preflight",
                "http_status": None,
                "response": None,
                "usage": {},
                "cost": None,
                "latency_ms": 0.0,
            }
        return self._conversation_service.complete(
            provider_id,
            model_id,
            messages,
            request_id,
            trace_id,
            external_processing_confirmed,
        )

    def _profile_for_credential(self, provider_id: str, model_id: str | None) -> ProviderProfile:
        profile = self._profiles.get(provider_id) if isinstance(provider_id, str) else None
        if profile is None or not profile.enabled or profile.secret_ref is None:
            raise ValueError("provider profile is not available")
        if model_id is not None and model_id != profile.model:
            raise ValueError("provider model is not available")
        return profile

    def _store_credential(self, profile: ProviderProfile, api_key: str) -> None:
        if not isinstance(api_key, str) or len(api_key) < 8 or len(api_key) > 4096 or api_key != api_key.strip():
            raise ValueError("provider credential is invalid")
        self._secret_resolver.put(profile.secret_ref, api_key)
        self._secret_resolver.put(
            _credential_status_reference(profile), "provider-configured-v1"
        )

    @staticmethod
    def _credential_result(profile: ProviderProfile) -> dict[str, object]:
        storage = "OS_SECURE_STORAGE" if profile.secret_ref and profile.secret_ref.startswith("secret://os/") else "CLOUD_SECRET_MANAGER"
        return {"profile_id": profile.provider_id, "configured": True, "storage": storage}


def _credential_status_reference(profile: ProviderProfile) -> SecretRef:
    credential_ref = SecretRef.parse(profile.secret_ref)
    identifier = sha256(
        f"{profile.provider_id}\0{credential_ref}".encode("utf-8")
    ).hexdigest()
    return SecretRef(
        provider=credential_ref.provider,
        name=f"shuliu-agent/provider-status/{identifier}",
    )


def create_provider_ipc_handlers(runtime: ProviderRuntime):
    """Create a fixed allowlist of provider IPC methods with safe response shapes."""
    from shuliu_runtime.ipc.server import IpcRequestCancelled

    def require_payload(params, keys: tuple[str, ...]):
        if not isinstance(params, dict) or set(params) != set(keys):
            raise ValueError("provider request is invalid")
        return params

    def check_cancelled(cancellation):
        if cancellation.is_set():
            raise IpcRequestCancelled()

    def list_profiles(params, cancellation):
        check_cancelled(cancellation)
        require_payload(params, ())
        return runtime.list_profiles()

    def save_profile(params, cancellation):
        check_cancelled(cancellation)
        values = require_payload(params, ("provider_id", "model_id", "api_key"))
        return runtime.save_profile(values["provider_id"], values["model_id"], values["api_key"])

    def replace_credential(params, cancellation):
        check_cancelled(cancellation)
        values = require_payload(params, ("profile_id", "api_key"))
        return runtime.replace_credential(values["profile_id"], values["api_key"])

    def test_connection(params, cancellation):
        check_cancelled(cancellation)
        if not isinstance(params, dict) or set(params) not in (
            {"provider_id", "capability"}, {"provider_id", "capability", "trace_id"},
        ):
            raise ValueError("provider request is invalid")
        result = runtime.test_connection(params["provider_id"], params["capability"], params.get("trace_id"))
        check_cancelled(cancellation)
        return result

    return {
        "providers.list": list_profiles,
        "providers.save_profile": save_profile,
        "providers.replace_credential": replace_credential,
        "providers.test_connection": test_connection,
    }


def create_chat_ipc_handlers(runtime: ProviderRuntime):
    from .conversation import create_conversation_ipc_handlers

    return create_conversation_ipc_handlers(runtime)


class _ConnectionTestBudget:
    """Process-local canary cap; durable financial accounting is Task 29."""

    def __init__(self, max_probes: int = 3) -> None:
        if isinstance(max_probes, bool) or not isinstance(max_probes, int) or max_probes < 1:
            raise ValueError("connection test probe limit must be positive")
        self._max_probes = max_probes
        self._accepted_probes = 0
        self._lock = threading.Lock()
        self._active: set[str] = set()

    def reserve(self, organization_id, budget_ref, provider_id, capability, trace_id, *, call_id) -> bool:
        with self._lock:
            if self._active or self._accepted_probes >= self._max_probes:
                return False
            self._active.add(call_id)
            self._accepted_probes += 1
            return True

    def settle(self, organization_id, budget_ref, call_id, usage, cost) -> None:
        with self._lock:
            self._active.discard(call_id)

    def release(self, organization_id, budget_ref, call_id) -> None:
        with self._lock:
            self._active.discard(call_id)

    def mark_unknown(self, organization_id, budget_ref, call_id) -> None:
        with self._lock:
            self._active.discard(call_id)


class _ConfiguredProviderHealth:
    def __init__(self, profiles: tuple[ProviderProfile, ...]) -> None:
        self._enabled = {profile.provider_id for profile in profiles if profile.enabled}

    def is_healthy(self, provider_id: str) -> bool:
        return provider_id in self._enabled


class _ConnectionCallSink:
    """Keeps only bounded, non-prompt ModelCall metadata until Task 29 persistence."""

    def __init__(self, capacity: int = 128) -> None:
        self._capacity = capacity
        self._records: list[object] = []

    def record(self, call) -> None:
        self._records.append(call)
        if len(self._records) > self._capacity:
            del self._records[0]


class _UnavailableSecretStore:
    def get(self, reference):
        raise SecretStoreUnavailableError("OS secure storage is unavailable")

    def put(self, reference, value):
        raise SecretStoreUnavailableError("OS secure storage is unavailable")

    def delete(self, reference):
        raise SecretStoreUnavailableError("OS secure storage is unavailable")


def create_provider_runtime_from_environment(
    *, chat_principal: ProductionIdentity | None = None, chat_policy: object | None = None
) -> ProviderRuntime:
    profiles = load_provider_profiles()
    organization_id = os.environ.get("SHULIU_ORGANIZATION_ID", "local")
    subject_id = "local-desktop-operator"
    if not _SAFE_IDENTIFIER.fullmatch(organization_id):
        raise ProviderConfigurationError("provider authorization context is invalid")

    try:
        os_store = OSSecretStore()
    except Exception:
        os_store = _UnavailableSecretStore()
    secret_resolver = SecretResolver({"os": os_store})

    policy_rules = [
        PolicyRule(
            rule_id=f"provider-connection-{profile.provider_id}-{capability}",
            action=f"model.invoke.{capability}.{profile.provider_id}",
            risk="LOW",
            decision="ALLOW",
            organization_id=organization_id,
            permission="foundation.model.connection_test",
            resource_type="model_provider",
            classification="PUBLIC",
            privacy="PUBLIC",
        )
        for profile in profiles
        if profile.enabled
        for capability in sorted(profile.capabilities)
    ]
    policy = PolicyEngine(policy_rules, policy_version="provider-connection-v1")
    gateway = ModelGateway(
        providers=tuple(profile.to_model_provider_config() for profile in profiles if profile.enabled),
        adapter=LiteLLMAdapter(timeout_seconds=20.0, max_output_tokens=8),
        policy=policy,
        budget=_ConnectionTestBudget(),
        health=_ConfiguredProviderHealth(profiles),
        model_calls=_ConnectionCallSink(),
        secret_resolver=secret_resolver,
        fallback_enabled=False,
    )
    context = ProviderConnectionContext(
        organization_id=organization_id,
        subject_id=subject_id,
        permissions=("foundation.model.connection_test",),
        budget_ref=f"budget://{organization_id}/model-connection-test",
    )
    service = ProviderConnectionService(
        profiles,
        gateway,
        context,
        canary_enabled=os.environ.get("SHULIU_MODEL_CONNECTION_TEST") == "1",
    )

    conversation_service = None
    if (
        isinstance(chat_principal, ProductionIdentity)
        and callable(getattr(chat_policy, "evaluate", None))
        and organization_id == chat_principal.organization_id
    ):
        chat_profiles = tuple(
            profile for profile in profiles if profile.enabled and "fast_chat" in profile.capabilities
        )
        chat_gateway = ModelGateway(
            providers=tuple(profile.to_model_provider_config() for profile in chat_profiles),
            adapter=LiteLLMAdapter(timeout_seconds=45.0, max_output_tokens=512),
            policy=chat_policy,
            budget=LocalConversationBudget(max_requests=20),
            health=_ConfiguredProviderHealth(chat_profiles),
            model_calls=_ConnectionCallSink(),
            secret_resolver=secret_resolver,
            fallback_enabled=False,
        )
        chat_context = ConversationContext(
            organization_id=chat_principal.organization_id,
            subject_id=chat_principal.actor_id,
            permissions=chat_principal.permissions,
            budget_ref=f"budget://{chat_principal.organization_id}/desktop-chat",
        )
        conversation_service = ConversationService(chat_profiles, chat_gateway, chat_context)
    return ProviderRuntime(profiles, secret_resolver, service, conversation_service)


def connection_test_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one policy-controlled model provider canary.")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--capability", required=True)
    args = parser.parse_args(argv)
    if os.environ.get("SHULIU_MODEL_CONNECTION_TEST") != "1":
        print(json.dumps({
            "status": "blocked",
            "provider_id": args.provider if _SAFE_IDENTIFIER.fullmatch(args.provider) else "unknown",
            "model": "",
            "capability": args.capability if _SAFE_IDENTIFIER.fullmatch(args.capability) else "unknown",
            "latency_ms": 0.0,
            "trace_id": str(uuid4()),
            "safe_error_code": "MODEL_CONNECTION_TEST_DISABLED",
        }))
        return 2
    result = create_provider_runtime_from_environment().test_connection(args.provider, args.capability)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "connected" else 1


if __name__ == "__main__":
    raise SystemExit(connection_test_cli())
