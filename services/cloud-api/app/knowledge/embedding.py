from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from math import isfinite
import os
import re
from typing import Protocol


class EmbeddingConfigurationError(ValueError):
    """A profile or gateway result cannot safely be used by this Knowledge index."""


class EmbeddingIndexNotReadyError(RuntimeError):
    """The requested profile version is still being built or re-embedded."""


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    profile_id: str
    provider_id: str
    model: str
    dimensions: int
    privacy_levels: frozenset[str]
    index_version: int
    capability: str = "embedding"

    def __post_init__(self) -> None:
        if (
            not _safe_identifier(self.profile_id, 128)
            or not _safe_identifier(self.provider_id, 128)
            or not _safe_model(self.model)
            or isinstance(self.dimensions, bool)
            or not isinstance(self.dimensions, int)
            or not 1 <= self.dimensions <= 65535
            or isinstance(self.index_version, bool)
            or not isinstance(self.index_version, int)
            or self.index_version < 1
            or self.capability != "embedding"
            or not isinstance(self.privacy_levels, frozenset)
            or not self.privacy_levels
            or any(not _safe_privacy_level(level) for level in self.privacy_levels)
        ):
            raise EmbeddingConfigurationError("embedding profile is invalid")


def load_embedding_profile(raw: str | None = None) -> EmbeddingProfile:
    """Load the non-secret index profile from explicit JSON configuration."""
    serialized = os.environ.get("SHULIU_KNOWLEDGE_EMBEDDING_PROFILE_JSON", "") if raw is None else raw
    if not isinstance(serialized, str) or not 1 <= len(serialized) <= 8192:
        raise EmbeddingConfigurationError("embedding profile configuration is missing or invalid")
    try:
        data = json.loads(serialized)
    except (TypeError, json.JSONDecodeError):
        raise EmbeddingConfigurationError("embedding profile configuration is invalid") from None
    allowed = {
        "profile_id", "provider_id", "model", "dimensions", "privacy_levels", "index_version", "capability"
    }
    required = {"profile_id", "provider_id", "model", "dimensions", "privacy_levels", "index_version"}
    if not isinstance(data, Mapping) or set(data) - allowed or required - set(data):
        raise EmbeddingConfigurationError("embedding profile configuration is invalid")
    privacy_levels = data["privacy_levels"]
    if not isinstance(privacy_levels, list) or any(not isinstance(level, str) for level in privacy_levels):
        raise EmbeddingConfigurationError("embedding profile configuration is invalid")
    try:
        return EmbeddingProfile(
            profile_id=data["profile_id"],
            provider_id=data["provider_id"],
            model=data["model"],
            dimensions=data["dimensions"],
            privacy_levels=frozenset(privacy_levels),
            index_version=data["index_version"],
            capability=data.get("capability", "embedding"),
        )
    except (TypeError, ValueError):
        raise EmbeddingConfigurationError("embedding profile configuration is invalid") from None


class ModelGatewayPort(Protocol):
    """The Gateway owns policy, budget, provider health, and SecretRef resolution."""

    def complete_for_provider(
        self,
        provider_id: str,
        request: Mapping[str, object],
        *,
        model: str,
    ) -> object: ...


class EmbeddingPort(Protocol):
    profile: EmbeddingProfile

    def embed(
        self,
        texts: Sequence[str],
        *,
        privacy: str,
        budget_ref: str,
        trace_id: str,
        organization_id: str,
        subject_id: str,
        permissions: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]: ...


class GatewayEmbeddingAdapter:
    """Binds Knowledge embedding calls to one explicit Gateway profile."""

    def __init__(self, gateway: ModelGatewayPort, profile: EmbeddingProfile) -> None:
        if gateway is None or not isinstance(profile, EmbeddingProfile):
            raise EmbeddingConfigurationError("embedding configuration is invalid")
        self._gateway = gateway
        self.profile = profile

    def embed(
        self,
        texts: Sequence[str],
        *,
        privacy: str,
        budget_ref: str,
        trace_id: str,
        organization_id: str,
        subject_id: str,
        permissions: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]:
        if (
            isinstance(texts, (str, bytes))
            or not isinstance(texts, Sequence)
            or not texts
            or any(not _bounded_text(value, 1_000_000) for value in texts)
            or not isinstance(privacy, str)
            or privacy not in self.profile.privacy_levels
            or not all(_bounded_text(value, 512) for value in (budget_ref, trace_id, organization_id, subject_id))
            or not isinstance(permissions, tuple)
            or not permissions
            or any(not _bounded_text(value, 256) for value in permissions)
        ):
            raise EmbeddingConfigurationError("embedding request is invalid")

        result = self._gateway.complete_for_provider(
            self.profile.provider_id,
            {
                "capability": self.profile.capability,
                "privacy": privacy,
                "budget_ref": budget_ref,
                "input": list(texts),
                "trace_id": trace_id,
                "organization_id": organization_id,
                "subject_id": subject_id,
                "permissions": permissions,
            },
            model=self.profile.model,
        )
        if (
            getattr(result, "resolved_provider", None) != self.profile.provider_id
            or getattr(result, "resolved_model", None) != self.profile.model
        ):
            raise EmbeddingConfigurationError("embedding gateway route does not match profile")
        raw_vectors = getattr(result, "output", None)
        if (
            not isinstance(raw_vectors, Sequence)
            or isinstance(raw_vectors, (str, bytes))
            or len(raw_vectors) != len(texts)
        ):
            raise EmbeddingConfigurationError("embedding gateway response is invalid")
        return tuple(_validated_vector(vector, self.profile.dimensions) for vector in raw_vectors)


def _validated_vector(value: object, dimensions: int) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != dimensions:
        raise EmbeddingConfigurationError("embedding vector dimensions do not match profile")
    result: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not isfinite(float(item)):
            raise EmbeddingConfigurationError("embedding vector is invalid")
        result.append(float(item))
    return tuple(result)


def _bounded_text(value: object, limit: int) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= limit


def _safe_identifier(value: object, limit: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= limit
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", value) is not None
    )


def _safe_model(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= 256
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value) is not None
        and not re.search(r"(?:sk-|rk-|secret://|https?://|api[_-]?key|bearer[:\s])", value, re.I)
    )


def _safe_privacy_level(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value) is not None
