from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re

from shuliu_runtime.security.secret_ref import InvalidSecretReferenceError, SecretRef


_DASHSCOPE_EMBEDDING_BASE = re.compile(
    r"(?i:https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"\.(?:cn-beijing|ap-southeast-1)\.maas\.aliyuncs\.com)"
    r"/compatible-mode/v1\Z",
)
_DASHSCOPE_EMBEDDING_DIMENSIONS = frozenset({1024, 768, 512, 256, 128, 64})


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
    embedding_api_base: str | None = field(default=None, repr=False)
    embedding_dimensions: int | None = None

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
        has_embedding_route = self.embedding_api_base is not None or self.embedding_dimensions is not None
        is_dashscope_embedding = (
            self.provider_id.casefold() == "dashscope"
            and ModelCapability.EMBEDDING.value in self.capabilities
        )
        if is_dashscope_embedding or has_embedding_route:
            if (
                self.provider_id.casefold() != "dashscope"
                or self.model.casefold() != "text-embedding-v3"
                or self.capabilities != frozenset({ModelCapability.EMBEDDING.value})
                or self.is_local
                or not isinstance(self.embedding_api_base, str)
                or _DASHSCOPE_EMBEDDING_BASE.fullmatch(self.embedding_api_base) is None
                or isinstance(self.embedding_dimensions, bool)
                or not isinstance(self.embedding_dimensions, int)
                or self.embedding_dimensions not in _DASHSCOPE_EMBEDDING_DIMENSIONS
            ):
                raise ValueError("provider embedding route is invalid")


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
