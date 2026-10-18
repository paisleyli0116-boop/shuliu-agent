from __future__ import annotations

import hashlib
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from shuliu_runtime.skills.manifest import (
    ManifestError,
    SkillManifest,
    load_manifest,
    version_satisfies,
)


class SignatureVerifier(Protocol):
    def verify(self, public_key: bytes, payload: bytes, signature: bytes) -> bool: ...


class Ed25519SignatureVerifier:
    """Replaceable verifier for publisher signatures over canonical manifest bytes."""

    def verify(self, public_key: bytes, payload: bytes, signature: bytes) -> bool:
        try:
            Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False


class PackageVerificationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PackageVerifier:
    def __init__(
        self,
        *,
        trusted_publishers: Mapping[str, bytes],
        foundation_version: str,
        skill_protocol: int,
        signature_verifier: SignatureVerifier | None = None,
        max_package_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        if not isinstance(trusted_publishers, Mapping) or any(
            not isinstance(publisher, str)
            or not publisher.strip()
            or not isinstance(public_key, bytes)
            for publisher, public_key in trusted_publishers.items()
        ):
            raise TypeError("trusted publisher keys are invalid")
        if isinstance(skill_protocol, bool) or not isinstance(skill_protocol, int) or skill_protocol < 1:
            raise ValueError("Skill protocol version is invalid")
        if isinstance(max_package_bytes, bool) or not isinstance(max_package_bytes, int) or max_package_bytes <= 0:
            raise ValueError("maximum package size must be positive")
        self._trusted_publishers = dict(trusted_publishers)
        self._foundation_version = foundation_version
        self._skill_protocol = skill_protocol
        self._signature_verifier = signature_verifier or Ed25519SignatureVerifier()
        self._max_package_bytes = max_package_bytes

    def verify(
        self, package: str | Path, *, installed_skills: Mapping[str, str]
    ) -> SkillManifest:
        root = Path(package)
        try:
            if root.is_symlink() or not root.is_dir():
                raise PackageVerificationError("SKILL_PACKAGE_INVALID", "Skill package directory is invalid")
            files = _package_files(root, max_bytes=self._max_package_bytes)
            manifest = load_manifest(root / "manifest.yaml")
        except ManifestError as error:
            raise PackageVerificationError("SKILL_MANIFEST_INVALID", str(error)) from error
        except OSError as error:
            raise PackageVerificationError("SKILL_PACKAGE_INVALID", "Skill package cannot be read") from error

        public_key = self._trusted_publishers.get(manifest.publisher)
        if public_key is None:
            raise PackageVerificationError(
                "SKILL_PUBLISHER_UNTRUSTED", "Skill publisher is not trusted"
            )
        if not self._signature_verifier.verify(
            public_key, manifest.signed_payload(), manifest.signature_bytes
        ):
            raise PackageVerificationError(
                "SKILL_SIGNATURE_INVALID", "Skill publisher signature is invalid"
            )

        actual_checksum = checksum_files(root, files)
        if manifest.checksum != f"sha256:{actual_checksum}":
            raise PackageVerificationError(
                "SKILL_CHECKSUM_INVALID", "Skill package checksum does not match"
            )
        try:
            if not version_satisfies(self._foundation_version, manifest.foundation_range):
                raise PackageVerificationError(
                    "SKILL_FOUNDATION_INCOMPATIBLE",
                    "Skill does not support this Foundation version",
                )
        except ManifestError as error:
            raise PackageVerificationError(
                "SKILL_FOUNDATION_RANGE_INVALID", "Skill Foundation range is invalid"
            ) from error
        if manifest.skill_protocol != self._skill_protocol:
            raise PackageVerificationError(
                "SKILL_PROTOCOL_INCOMPATIBLE", "Skill protocol version is not supported"
            )
        for dependency_id, version_range in manifest.dependencies.items():
            installed_version = installed_skills.get(dependency_id)
            if installed_version is None or not version_satisfies(installed_version, version_range):
                raise PackageVerificationError(
                    "SKILL_DEPENDENCY_UNSATISFIED",
                    "A required Skill dependency is missing or incompatible",
                )
        return manifest


def checksum_files(root: Path, files: tuple[Path, ...] | None = None) -> str:
    paths = files if files is not None else _package_files(root, max_bytes=100 * 1024 * 1024)
    digest = hashlib.sha256()
    digest.update(b"SHULIU-SKILL-PACKAGE\0v1\0")
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if relative == "manifest.yaml":
            continue
        relative_bytes = relative.encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _package_files(root: Path, *, max_bytes: int) -> tuple[Path, ...]:
    paths: list[Path] = []
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        try:
            metadata = path.lstat()
        except OSError as error:
            raise PackageVerificationError(
                "SKILL_PACKAGE_INVALID", "Skill package entry cannot be inspected"
            ) from error
        if stat.S_ISLNK(metadata.st_mode):
            raise PackageVerificationError(
                "SKILL_PACKAGE_SYMLINK", "Skill packages cannot contain symbolic links"
            )
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise PackageVerificationError(
                "SKILL_PACKAGE_INVALID", "Skill package contains a non-regular file"
            )
        total_bytes += metadata.st_size
        if total_bytes > max_bytes:
            raise PackageVerificationError(
                "SKILL_PACKAGE_TOO_LARGE", "Skill package exceeds the configured size limit"
            )
        paths.append(path)
    if not any(path.relative_to(root).as_posix() == "manifest.yaml" for path in paths):
        raise PackageVerificationError(
            "SKILL_MANIFEST_MISSING", "Skill package has no root manifest.yaml"
        )
    return tuple(paths)
