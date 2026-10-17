from __future__ import annotations

import re
from dataclasses import dataclass


_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*"
_REFERENCE = re.compile(rf"secret://(?P<provider>os|cloud)/(?P<name>{_NAME})\Z")


class InvalidSecretReferenceError(ValueError):
    """Raised when a reference is malformed or targets an unknown provider."""


@dataclass(frozen=True)
class SecretRef:
    provider: str
    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or self.provider not in {"os", "cloud"}:
            raise InvalidSecretReferenceError("secret reference provider is invalid")
        if not isinstance(self.name, str) or not re.fullmatch(_NAME, self.name):
            raise InvalidSecretReferenceError("secret reference name is invalid")

    @classmethod
    def parse(cls, value: str) -> SecretRef:
        if not isinstance(value, str):
            raise InvalidSecretReferenceError("secret reference must be text")
        match = _REFERENCE.fullmatch(value)
        if match is None:
            raise InvalidSecretReferenceError("secret reference is invalid")
        return cls(provider=match.group("provider"), name=match.group("name"))

    def __str__(self) -> str:
        return f"secret://{self.provider}/{self.name}"


def parse_secret_ref(value: str) -> SecretRef:
    return SecretRef.parse(value)
