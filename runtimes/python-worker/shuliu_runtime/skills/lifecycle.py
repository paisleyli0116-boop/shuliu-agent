from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from shuliu_runtime.skills.manifest import ManifestError, SkillManifest, load_manifest
from shuliu_runtime.skills.registry import SkillRegistry, SkillRegistryError, SkillState
from shuliu_runtime.skills.signature import PackageVerificationError, PackageVerifier


class SkillLifecycleError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PermissionChangeApproval(Protocol):
    """Approval port; implementations persist the permission-change audit event before deciding."""

    def is_approved(
        self,
        skill_id: str,
        from_version: str,
        to_version: str,
        added_permissions: tuple[str, ...],
    ) -> bool: ...


class SkillUsage(Protocol):
    """Reports AgentRun, Workflow or Schedule references that pin a Skill version."""

    def is_in_use(self, skill_id: str, version: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class SkillInstallResult:
    skill_id: str
    version: str
    state: SkillState
    active_version: str


class SkillInstaller:
    """Verifies, stages and atomically activates a trusted declarative Skill package."""

    def __init__(
        self,
        *,
        registry: SkillRegistry,
        verifier: PackageVerifier,
        contract_test: Callable[[SkillManifest], bool],
        usage: SkillUsage,
        permission_approver: PermissionChangeApproval | None = None,
    ) -> None:
        if not callable(contract_test):
            raise TypeError("Skill contract test callback is required")
        if not callable(getattr(usage, "is_in_use", None)):
            raise TypeError("Skill usage port is required")
        self.registry = registry
        self.verifier = verifier
        self.contract_test = contract_test
        self.usage = usage
        self.permission_approver = permission_approver

    def install(self, package: str | Path) -> SkillInstallResult:
        package_path = Path(package)
        try:
            manifest = load_manifest(package_path / "manifest.yaml")
        except ManifestError as error:
            raise SkillLifecycleError("SKILL_MANIFEST_INVALID", "Skill manifest is invalid") from error

        try:
            self.registry.observe(manifest)
            existing = self.registry.installed_versions(manifest.skill_id)
            if manifest.version in existing:
                verified = self.verifier.verify(
                    package_path, installed_skills=self.registry.active_versions()
                )
                stored = self.registry.manifest(manifest.skill_id, manifest.version)
                if (
                    stored.checksum != verified.checksum
                    or stored.signed_payload() != verified.signed_payload()
                ):
                    raise SkillLifecycleError(
                        "SKILL_VERSION_CONFLICT",
                        "Installed Skill version has different content or metadata",
                    )
                self.registry.activate(manifest.skill_id, manifest.version)
                return SkillInstallResult(
                    manifest.skill_id,
                    manifest.version,
                    SkillState.ENABLED,
                    manifest.version,
                )
        except PackageVerificationError as error:
            raise SkillLifecycleError(error.code, str(error)) from error
        except SkillRegistryError as error:
            raise SkillLifecycleError(error.code, str(error)) from error

        provisional = True
        stage_path: Path | None = None
        installed = False
        try:
            self.registry.transition(
                manifest.skill_id, manifest.version, SkillState.DOWNLOADED
            )
            verified = self.verifier.verify(
                package_path, installed_skills=self.registry.active_versions()
            )
            self.registry.transition(
                manifest.skill_id, manifest.version, SkillState.VERIFIED
            )
            self._require_permission_approval(verified)

            stage_path = self.registry.stage(package_path, verified)
            # Re-verify the copied bytes to close a source mutation window during staging.
            staged_manifest = self.verifier.verify(
                stage_path, installed_skills=self.registry.active_versions()
            )
            if staged_manifest.checksum != verified.checksum:
                raise SkillLifecycleError(
                    "SKILL_CHECKSUM_INVALID", "Staged Skill content changed during copy"
                )
            self.registry.transition(
                manifest.skill_id, manifest.version, SkillState.STAGED
            )
            try:
                contract_passed = self.contract_test(staged_manifest)
            except Exception as error:
                raise SkillLifecycleError(
                    "SKILL_CONTRACT_TEST_FAILED", "Skill contract validation failed"
                ) from error
            if contract_passed is not True:
                raise SkillLifecycleError(
                    "SKILL_CONTRACT_TEST_FAILED", "Skill contract validation failed"
                )

            self.registry.install_staged(stage_path, verified)
            stage_path = None
            installed = True
            self.registry.transition(
                manifest.skill_id, manifest.version, SkillState.INSTALLED
            )
            self.registry.activate(manifest.skill_id, manifest.version)
            provisional = False
            return SkillInstallResult(
                manifest.skill_id,
                manifest.version,
                SkillState.ENABLED,
                manifest.version,
            )
        except PackageVerificationError as error:
            raise SkillLifecycleError(error.code, str(error)) from error
        except SkillRegistryError as error:
            raise SkillLifecycleError(error.code, str(error)) from error
        finally:
            if stage_path is not None:
                self.registry.abandon_staging(stage_path)
            if provisional and not installed:
                self.registry.discard_uninstalled(manifest.skill_id, manifest.version)

    def rollback(self, skill_id: str, version: str) -> str:
        try:
            if version not in self.registry.installed_versions(skill_id):
                raise SkillLifecycleError(
                    "SKILL_ROLLBACK_VERSION_UNAVAILABLE", "Rollback Skill version is unavailable"
                )
            self.registry.activate(skill_id, version)
            return version
        except SkillRegistryError as error:
            raise SkillLifecycleError(error.code, str(error)) from error

    def disable(self, skill_id: str, version: str) -> None:
        try:
            self.registry.disable(skill_id, version)
        except SkillRegistryError as error:
            raise SkillLifecycleError(error.code, str(error)) from error

    def deprecate(self, skill_id: str, version: str) -> None:
        try:
            self.registry.deprecate(skill_id, version)
        except SkillRegistryError as error:
            raise SkillLifecycleError(error.code, str(error)) from error

    def remove(self, skill_id: str, version: str) -> None:
        try:
            if self.usage.is_in_use(skill_id, version):
                raise SkillLifecycleError(
                    "SKILL_IN_USE", "Skill version is pinned by an active AgentRun"
                )
            dependents = self.registry.active_dependents(skill_id, version)
            if dependents:
                raise SkillLifecycleError(
                    "SKILL_DEPENDENCY_IN_USE",
                    "An enabled Skill depends on this version",
                )
            self.registry.remove(skill_id, version)
        except SkillRegistryError as error:
            raise SkillLifecycleError(error.code, str(error)) from error

    def _require_permission_approval(self, manifest: SkillManifest) -> None:
        active = self.registry.active_version(manifest.skill_id)
        if active is None or active == manifest.version:
            return
        current = self.registry.manifest(manifest.skill_id, active)
        added = tuple(sorted(set(manifest.permissions) - set(current.permissions)))
        if not added:
            return
        approver = self.permission_approver
        approved = bool(
            approver
            and approver.is_approved(
                manifest.skill_id, active, manifest.version, added
            )
        )
        if not approved:
            raise SkillLifecycleError(
                "PERMISSION_CHANGE_DETECTED",
                "Skill permission increase requires administrator approval",
            )
