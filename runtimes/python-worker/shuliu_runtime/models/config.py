from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .capability import ModelCapability, ModelProviderConfig, parse_capability


_PROVIDER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}\Z")
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_JWT_LIKE_MODEL = re.compile(
    r"(?:^|/)eyJ[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]{8,}){2}(?:/|$)"
)
_OPAQUE_MODEL_COMPONENT = re.compile(r"[A-Za-z0-9_-]{32,}")
_SECRET_LIKE_MODEL = re.compile(
    r"(?:sk-|rk-|pk-|glpat-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|ya29\.[A-Za-z0-9_-]{16,}|api[_-]?key[:_-]|bearer[:_\s]|token[:_-]|secret://|https?://)",
    re.IGNORECASE,
)
_ALLOWED_FIELDS = frozenset(
    {
        "provider_id", "model", "capabilities", "priority", "is_local", "secret_ref", "enabled",
        "embedding_api_base", "embedding_dimensions",
    }
)
_REQUIRED_FIELDS = frozenset(
    {"provider_id", "model", "capabilities", "priority", "is_local"}
)
_PROFILES_ENV = "SHULIU_MODEL_PROFILES_JSON"


class ProviderConfigurationError(ValueError):
    """Provider settings are invalid; messages never include configuration values."""


@dataclass(frozen=True)
class ProviderProfile:
    provider_id: str
    model: str
    capabilities: frozenset[str]
    priority: int
    is_local: bool
    secret_ref: str | None
    enabled: bool = True
    embedding_api_base: str | None = field(default=None, repr=False)
    embedding_dimensions: int | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.provider_id, str)
            or not _PROVIDER_ID.fullmatch(self.provider_id)
            or _JWT_LIKE_MODEL.search(self.provider_id)
            or _OPAQUE_MODEL_COMPONENT.search(self.provider_id)
            or _SECRET_LIKE_MODEL.search(self.provider_id)
        ):
            raise ProviderConfigurationError("provider ID is invalid")
        if (
            not isinstance(self.model, str)
            or not _MODEL_ID.fullmatch(self.model)
            or _JWT_LIKE_MODEL.search(self.model)
            or _OPAQUE_MODEL_COMPONENT.search(self.model)
            or _SECRET_LIKE_MODEL.search(self.model)
        ):
            raise ProviderConfigurationError("provider model is invalid")
        if (
            not isinstance(self.capabilities, frozenset)
            or not self.capabilities
            or any(not isinstance(item, str) for item in self.capabilities)
        ):
            raise ProviderConfigurationError("provider capabilities are invalid")
        try:
            for capability in self.capabilities:
                parse_capability(capability)
        except ValueError:
            raise ProviderConfigurationError("provider capability is unsupported") from None
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise ProviderConfigurationError("provider priority is invalid")
        if not isinstance(self.is_local, bool) or not isinstance(self.enabled, bool):
            raise ProviderConfigurationError("provider flags are invalid")
        try:
            ModelProviderConfig(
                provider_id=self.provider_id,
                model=self.model,
                capabilities=self.capabilities,
                priority=self.priority,
                is_local=self.is_local,
                secret_ref=self.secret_ref,
                embedding_api_base=self.embedding_api_base,
                embedding_dimensions=self.embedding_dimensions,
            )
        except (TypeError, ValueError):
            raise ProviderConfigurationError("provider profile configuration is invalid") from None
        if self.secret_ref is None and not self.is_local:
            raise ProviderConfigurationError("remote provider requires a secret reference")

    def to_model_provider_config(self) -> ModelProviderConfig:
        return ModelProviderConfig(
            provider_id=self.provider_id,
            model=self.model,
            capabilities=self.capabilities,
            priority=self.priority,
            is_local=self.is_local,
            secret_ref=self.secret_ref,
            embedding_api_base=self.embedding_api_base,
            embedding_dimensions=self.embedding_dimensions,
        )


def load_provider_profiles(raw: str | None = None) -> tuple[ProviderProfile, ...]:
    """Read strict JSON profiles from an explicit value or the process environment.

    API keys are deliberately not accepted as profile fields. Remote credentials
    are referenced only by SecretRef and resolved by the Model Gateway.
    """
    serialized = os.environ.get(_PROFILES_ENV, "[]") if raw is None else raw
    if not isinstance(serialized, str) or len(serialized) > 65_536:
        raise ProviderConfigurationError("provider configuration is invalid")
    try:
        decoded: Any = json.loads(serialized)
    except (TypeError, json.JSONDecodeError):
        raise ProviderConfigurationError("provider configuration is invalid") from None
    if not isinstance(decoded, list):
        raise ProviderConfigurationError("provider configuration must be a list")

    profiles: list[ProviderProfile] = []
    seen_ids: set[str] = set()
    for item in decoded:
        if not isinstance(item, Mapping):
            raise ProviderConfigurationError("provider profile is invalid")
        keys = frozenset(item)
        if keys - _ALLOWED_FIELDS or _REQUIRED_FIELDS - keys:
            raise ProviderConfigurationError("provider profile fields are invalid")
        provider_id = item["provider_id"]
        if isinstance(provider_id, str) and provider_id in seen_ids:
            raise ProviderConfigurationError("provider IDs must be unique")
        capabilities = item["capabilities"]
        if not isinstance(capabilities, list) or any(not isinstance(value, str) for value in capabilities):
            raise ProviderConfigurationError("provider capabilities are invalid")
        profile = ProviderProfile(
            provider_id=provider_id,
            model=item["model"],
            capabilities=frozenset(capabilities),
            priority=item["priority"],
            is_local=item["is_local"],
            secret_ref=item.get("secret_ref"),
            enabled=item.get("enabled", True),
            embedding_api_base=item.get("embedding_api_base"),
            embedding_dimensions=item.get("embedding_dimensions"),
        )
        seen_ids.add(profile.provider_id)
        profiles.append(profile)
    return tuple(sorted(profiles, key=lambda profile: (profile.priority, profile.provider_id)))
