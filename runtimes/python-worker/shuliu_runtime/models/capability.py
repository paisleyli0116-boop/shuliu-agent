from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from shuliu_runtime.security.secret_ref import InvalidSecretReferenceError, SecretRef


class ModelCapability(str, Enum):
    FAST_CHAT = "fast_chat"
    REASONING_HIGH = "reasoning_high"
    VISION = "vision"
    EMBEDDING = "embedding"


@dataclass(frozen=True)
class ModelProviderConfig:
    provider_id: str
    model: str
    capabilities: frozenset[str]
    priority: int
    is_local: bool
    secret_ref: str | None = None

    def __post_init__(self) -> None:
        if not _non_empty(self.provider_id) or not _non_empty(self.model):
            raise ValueError("provider ID and model are required")
        if not isinstance(self.capabilities, frozenset) or not self.capabilities:
            raise ValueError("provider capabilities must be a non-empty frozenset")
        if any(not _non_empty(capability) for capability in self.capabilities):
            raise ValueError("provider capability is invalid")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise ValueError("provider priority must be an integer")
        if not isinstance(self.is_local, bool):
            raise ValueError("provider locality must be explicit")
        if self.secret_ref is not None and not _non_empty(self.secret_ref):
            raise ValueError("provider secret reference is invalid")
        if self.secret_ref is not None:
            try:
                SecretRef.parse(self.secret_ref)
            except InvalidSecretReferenceError as error:
                raise ValueError("provider secret reference is invalid") from error


def parse_capability(value: ModelCapability | str) -> ModelCapability:
    if isinstance(value, ModelCapability):
        return value
    if not isinstance(value, str):
        raise ValueError("model capability is invalid")
    try:
        return ModelCapability(value)
    except ValueError as error:
        raise ValueError("model capability is unsupported") from error


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
