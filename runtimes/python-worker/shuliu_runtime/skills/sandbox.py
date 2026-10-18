from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

REQUIRED_SANDBOX_CONTROLS = frozenset(
    {
        "separate_process",
        "filesystem_scope",
        "network_scope",
        "secret_isolation",
        "foundation_db_isolation",
        "no_direct_os_control",
    }
)


class SandboxUnavailableError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    skill_id: str
    permissions: tuple[str, ...]
    filesystem_roots: tuple[Path, ...] = ()
    network_allowlist: tuple[str, ...] = ()
    secret_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.skill_id, str) or not self.skill_id.strip():
            raise SandboxUnavailableError(
                "SKILL_POLICY_INVALID", "Skill ID is required"
            )
        fields = (
            ("permissions", self.permissions, str),
            ("filesystem_roots", self.filesystem_roots, Path),
            ("network_allowlist", self.network_allowlist, str),
            ("secret_refs", self.secret_refs, str),
        )
        for name, values, value_type in fields:
            if not isinstance(values, tuple) or any(
                not isinstance(value, value_type) or not str(value).strip()
                for value in values
            ):
                raise SandboxUnavailableError(
                    "SKILL_POLICY_INVALID", f"Skill {name} policy is invalid"
                )
            if len(set(map(str, values))) != len(values):
                raise SandboxUnavailableError(
                    "SKILL_POLICY_INVALID", f"Skill {name} policy contains duplicates"
                )
        if self.secret_refs:
            raise SandboxUnavailableError(
                "SKILL_SECRET_ACCESS_DENIED",
                "Executable Skills cannot receive Secret references or values",
            )


@dataclass(frozen=True, slots=True)
class SandboxAssessment:
    supported: bool
    enforced_controls: frozenset[str]
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.supported, bool):
            raise SandboxUnavailableError(
                "SKILL_SANDBOX_UNAVAILABLE", "Sandbox support flag is invalid"
            )
        if not isinstance(self.enforced_controls, frozenset) or any(
            not isinstance(control, str) or not control.strip()
            for control in self.enforced_controls
        ):
            raise SandboxUnavailableError(
                "SKILL_SANDBOX_UNAVAILABLE", "Sandbox control evidence is invalid"
            )
        if not isinstance(self.reason, str):
            raise SandboxUnavailableError(
                "SKILL_SANDBOX_UNAVAILABLE", "Sandbox assessment reason is invalid"
            )


class SandboxProvider(Protocol):
    def assess(self, policy: SandboxPolicy) -> SandboxAssessment: ...

    def wrap_command(
        self, command: Sequence[str], policy: SandboxPolicy, working_directory: Path
    ) -> Sequence[str]: ...


class UnavailableSandboxProvider:
    """Fail-closed provider used until a platform adapter is security-validated."""

    def assess(self, policy: SandboxPolicy) -> SandboxAssessment:
        del policy
        return SandboxAssessment(
            supported=False,
            enforced_controls=frozenset(),
            reason="No verified OS sandbox provider is configured for this platform",
        )

    def wrap_command(
        self, command: Sequence[str], policy: SandboxPolicy, working_directory: Path
    ) -> Sequence[str]:
        del command, policy, working_directory
        raise SandboxUnavailableError(
            "SKILL_SANDBOX_UNAVAILABLE", "No verified OS sandbox provider is configured"
        )


def validate_skill_permissions(
    *, requested: Iterable[str], approved: Iterable[str]
) -> None:
    requested_permissions = _permission_set(requested, "requested")
    approved_permissions = _permission_set(approved, "approved")
    if not requested_permissions.issubset(approved_permissions):
        raise SandboxUnavailableError(
            "SKILL_PERMISSION_ESCALATION",
            "Skill requested permissions that have not been explicitly approved",
        )


def _permission_set(values: Iterable[str], label: str) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise SandboxUnavailableError(
            "SKILL_POLICY_INVALID", f"{label} permissions must be a sequence"
        )
    try:
        result = tuple(values)
    except TypeError as error:
        raise SandboxUnavailableError(
            "SKILL_POLICY_INVALID", f"{label} permissions must be iterable"
        ) from error
    if any(not isinstance(value, str) or not value.strip() for value in result):
        raise SandboxUnavailableError(
            "SKILL_POLICY_INVALID", f"{label} permissions contain an invalid value"
        )
    if len(set(result)) != len(result):
        raise SandboxUnavailableError(
            "SKILL_POLICY_INVALID", f"{label} permissions contain duplicates"
        )
    return frozenset(result)
