from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import uuid4

from shuliu_runtime.tools.broker import IdentityPrincipal

ACTOR_ENVIRONMENT_KEY = "SHULIU_ACTOR_ID"
ORGANIZATION_ENVIRONMENT_KEY = "SHULIU_ORGANIZATION_ID"
PERMISSIONS_ENVIRONMENT_KEY = "SHULIU_ACTOR_PERMISSIONS"
PRIVACY_ENVIRONMENT_KEY = "SHULIU_CONTEXT_PRIVACY"

# Permission codes are the dotted form defined by the Permission catalogue. A
# value that does not match is a configuration error, never a wildcard.
_PERMISSION_CODE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")


class ProductionIdentityUnavailable(RuntimeError):
    """No trustworthy identity is configured, so trusted composition must not start."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ProductionIdentity:
    """The acting identity of this Runtime process.

    The value is declared by the Desktop Main process when it launches the
    Sidecar. A Renderer can never set it, because it is read from the process
    environment that only the parent process controls.
    """

    actor_id: str
    organization_id: str
    permissions: tuple[str, ...]
    # Data classification of this Runtime's Tool context. It is declared by the
    # same trusted launch configuration, never inferred from a request.
    privacy: str

    def actor(self) -> dict[str, object]:
        return {
            "subject_id": self.actor_id,
            "organization_id": self.organization_id,
            "permissions": list(self.permissions),
        }


@dataclass(frozen=True)
class IdentityResolution:
    identity: ProductionIdentity | None
    error_code: str | None


class EnvironmentIdentityProvider:
    """Resolve the single-company identity ADR-018 describes for Foundation V0.1.

    Absent configuration is reported as unavailable rather than defaulted: a
    missing variable must never silently become a privileged or anonymous
    subject.
    """

    def __init__(
        self,
        environment: Mapping[str, str],
        *,
        trace_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._environment = environment
        self._trace_id_factory = trace_id_factory or (lambda: str(uuid4()))

    def resolve(self) -> IdentityResolution:
        actor_id = self._value(ACTOR_ENVIRONMENT_KEY)
        organization_id = self._value(ORGANIZATION_ENVIRONMENT_KEY)
        raw_permissions = self._value(PERMISSIONS_ENVIRONMENT_KEY)
        privacy = self._value(PRIVACY_ENVIRONMENT_KEY)
        if (
            actor_id is None
            or organization_id is None
            or raw_permissions is None
            or privacy is None
        ):
            return IdentityResolution(None, "RUNTIME_IDENTITY_UNAVAILABLE")

        permissions = tuple(
            part.strip() for part in raw_permissions.split(",") if part.strip()
        )
        if not permissions:
            return IdentityResolution(None, "RUNTIME_IDENTITY_UNAVAILABLE")
        if len(set(permissions)) != len(permissions):
            return IdentityResolution(None, "RUNTIME_IDENTITY_INVALID")
        if any(not _PERMISSION_CODE.fullmatch(code) for code in permissions):
            return IdentityResolution(None, "RUNTIME_IDENTITY_INVALID")
        return IdentityResolution(
            ProductionIdentity(
                actor_id=actor_id,
                organization_id=organization_id,
                permissions=permissions,
                privacy=privacy,
            ),
            None,
        )

    def require(self) -> ProductionIdentity:
        resolution = self.resolve()
        if resolution.identity is None:
            raise ProductionIdentityUnavailable(
                resolution.error_code or "RUNTIME_IDENTITY_UNAVAILABLE"
            )
        return resolution.identity

    def tool_broker_identity(self) -> TrustedIdentityResolver:
        return TrustedIdentityResolver(self.require())

    def run_context(self) -> dict[str, object]:
        identity = self.require()
        return {
            "organization_id": identity.organization_id,
            "trace_id": str(self._trace_id_factory()),
            "privacy": identity.privacy,
            "actor": identity.actor(),
        }

    def _value(self, key: str) -> str | None:
        raw = self._environment.get(key)
        if not isinstance(raw, str):
            return None
        trimmed = raw.strip()
        return trimmed or None


class TrustedIdentityResolver:
    """Resolve Tool Broker principals from the process identity, not the request.

    The actor mapping the caller supplies is only a claim. It is honoured when
    it agrees with the trusted identity and refused otherwise; permissions are
    always taken from the trusted identity so a payload can never widen them.
    """

    def __init__(self, identity: ProductionIdentity) -> None:
        self._identity = identity

    def resolve(self, actor: Mapping[str, object]) -> IdentityPrincipal | None:
        if not isinstance(actor, Mapping):
            return None
        if actor.get("subject_id") != self._identity.actor_id:
            return None
        if actor.get("organization_id") != self._identity.organization_id:
            return None
        return IdentityPrincipal(
            subject_id=self._identity.actor_id,
            organization_id=self._identity.organization_id,
            permissions=self._identity.permissions,
        )
