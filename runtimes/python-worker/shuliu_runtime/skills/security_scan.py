"""Skill package security scanning.

Executes a real, operator-configured malware scanner against the exact bytes
that passed checksum verification, before any staging or activation.  Every
outcome other than a clean verdict from a fully identified engine fails
closed: timeout, missing engine, engine errors, unknown results and checksum
mismatches all refuse the package.

The scanner is an external process (no library linkage), so the engine's own
license does not attach to this code base.  Evidence records only verdict
metadata: package digest, engine identity and versions, and the scan time.
Raw scanner output, package contents and secrets never enter the evidence.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Protocol

from shuliu_runtime.skills.signature import (
    PackageVerificationError,
    checksum_files,
)


class ScanConfigurationError(ValueError):
    """Raised when the scan request itself is invalid (fail closed)."""


class ScanStatus(Enum):
    CLEAN = "CLEAN"
    DETECTED = "DETECTED"
    ERROR = "ERROR"


SCAN_ENGINE_UNAVAILABLE = "SCAN_ENGINE_UNAVAILABLE"
SCAN_TIMEOUT = "SCAN_TIMEOUT"
SCAN_ENGINE_ERROR = "SCAN_ENGINE_ERROR"
SCAN_CHECKSUM_MISMATCH = "SCAN_CHECKSUM_MISMATCH"
SCAN_PACKAGE_INVALID = "SCAN_PACKAGE_INVALID"
SCAN_ENGINE_VERSION_UNKNOWN = "SCAN_ENGINE_VERSION_UNKNOWN"


@dataclass(frozen=True, slots=True)
class ScanEvidence:
    status: ScanStatus
    package_digest: str
    engine: str
    engine_version: str
    ruleset_version: str
    scanned_at: datetime | None
    reason_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, ScanStatus):
            raise ScanConfigurationError("scan status is invalid")
        if self.status is ScanStatus.CLEAN:
            for name in ("package_digest", "engine", "engine_version", "ruleset_version"):
                if not isinstance(getattr(self, name), str) or not getattr(self, name):
                    raise ScanConfigurationError(
                        f"clean scan evidence requires {name}"
                    )
            if not isinstance(self.scanned_at, datetime) or self.scanned_at.tzinfo is None:
                raise ScanConfigurationError("clean scan evidence requires a timezone-aware timestamp")
            if self.reason_code:
                raise ScanConfigurationError("clean scan evidence must not carry a failure reason")
        if not isinstance(self.reason_code, str):
            raise ScanConfigurationError("scan reason code is invalid")


class SkillSecurityScanner(Protocol):
    def scan(
        self,
        package_path: Path,
        *,
        expected_checksum: str | None,
        timeout: float,
    ) -> ScanEvidence: ...


class ClamAvScanner:
    """Adapter for the ClamAV ``clamscan`` command line engine.

    ClamAV is GPL-2.0 licensed and invoked here strictly as an external
    process, so no licensing obligation attaches to this code base.  The
    binary path comes from deployment configuration; the runtime never
    searches the system for an engine on its own.
    """

    engine = "clamav"

    def __init__(self, scan_binary: str | Path, *, database_dir: str | Path | None = None) -> None:
        if not isinstance(scan_binary, (str, Path)) or not str(scan_binary).strip():
            raise ScanConfigurationError("scanner binary path is required")
        if database_dir is not None and not str(database_dir).strip():
            raise ScanConfigurationError("scanner database directory is invalid")
        self._scan_binary = str(scan_binary)
        self._database_dir = str(database_dir) if database_dir is not None else None

    def scan(
        self,
        package_path: Path,
        *,
        expected_checksum: str | None,
        timeout: float,
    ) -> ScanEvidence:
        scanned_at = datetime.now(UTC)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ScanConfigurationError("scan timeout must be a positive number")
        package = Path(package_path)
        try:
            digest = f"sha256:{checksum_files(package)}"
        except (PackageVerificationError, OSError):
            return self._error(SCAN_PACKAGE_INVALID, scanned_at)
        if expected_checksum is not None and expected_checksum != digest:
            return self._error(SCAN_CHECKSUM_MISMATCH, scanned_at, package_digest=digest)

        engine_version, ruleset_version = self._probe_version(timeout)
        if engine_version is None:
            return self._error(SCAN_ENGINE_UNAVAILABLE, scanned_at, package_digest=digest)
        if ruleset_version is None:
            return self._error(SCAN_ENGINE_VERSION_UNKNOWN, scanned_at, package_digest=digest)

        command = [self._scan_binary, "--infected", "--no-summary"]
        if self._database_dir is not None:
            command += ["--database", self._database_dir]
        command += ["--", str(package)]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return self._error(SCAN_TIMEOUT, scanned_at, package_digest=digest)
        except OSError:
            return self._error(SCAN_ENGINE_UNAVAILABLE, scanned_at, package_digest=digest)

        if completed.returncode == 0:
            return ScanEvidence(
                status=ScanStatus.CLEAN,
                package_digest=digest,
                engine=self.engine,
                engine_version=engine_version,
                ruleset_version=ruleset_version,
                scanned_at=scanned_at,
                reason_code="",
            )
        if completed.returncode == 1:
            return ScanEvidence(
                status=ScanStatus.DETECTED,
                package_digest=digest,
                engine=self.engine,
                engine_version=engine_version,
                ruleset_version=ruleset_version,
                scanned_at=scanned_at,
                reason_code="",
            )
        return self._error(SCAN_ENGINE_ERROR, scanned_at, package_digest=digest)

    def _probe_version(self, timeout: float) -> tuple[str | None, str | None]:
        command = [self._scan_binary, "--version"]
        if self._database_dir is not None:
            command += ["--database", self._database_dir]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None, None
        if completed.returncode != 0:
            return None, None
        output = completed.stdout
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        output = output.strip()
        # Example: "ClamAV 1.5.4/27423/Wed Oct 22 09:12:34 2026"
        parts = output.split("/")
        engine_fields = parts[0].split()
        if len(engine_fields) < 2:
            return None, None
        engine_version = engine_fields[1]
        ruleset_version = parts[1].strip() if len(parts) > 1 else ""
        if not ruleset_version:
            return engine_version, None
        return engine_version, ruleset_version

    @staticmethod
    def _error(
        reason_code: str, scanned_at: datetime, *, package_digest: str = ""
    ) -> ScanEvidence:
        return ScanEvidence(
            status=ScanStatus.ERROR,
            package_digest=package_digest,
            engine=ClamAvScanner.engine,
            engine_version="",
            ruleset_version="",
            scanned_at=scanned_at,
            reason_code=reason_code,
        )
