"""Test-only security scanner fake.

Unit tests may use this fake to exercise lifecycle wiring.  A fake scanner
can never satisfy the `skill_security_scan` Release Gate; that requires real
engine evidence (see tests/release/test_skill_security_gate.py).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from shuliu_runtime.skills.security_scan import ScanEvidence, ScanStatus
from shuliu_runtime.skills.signature import checksum_files


class FakeSkillScanner:
    engine = "fake"

    def __init__(self, status: ScanStatus = ScanStatus.CLEAN) -> None:
        self.status = status
        self.calls: list[tuple[Path, str | None, float]] = []

    def scan(
        self, package_path: Path, *, expected_checksum: str | None, timeout: float
    ) -> ScanEvidence:
        digest = f"sha256:{checksum_files(Path(package_path))}"
        self.calls.append((Path(package_path), expected_checksum, timeout))
        if self.status is ScanStatus.CLEAN and expected_checksum not in (None, digest):
            return ScanEvidence(
                status=ScanStatus.ERROR,
                package_digest=digest,
                engine=self.engine,
                engine_version="0.0.0",
                ruleset_version="fake-ruleset",
                scanned_at=datetime.now(UTC),
                reason_code="SCAN_CHECKSUM_MISMATCH",
            )
        return ScanEvidence(
            status=self.status,
            package_digest=digest,
            engine=self.engine,
            engine_version="0.0.0",
            ruleset_version="fake-ruleset",
            scanned_at=datetime.now(UTC),
            reason_code="",
        )
