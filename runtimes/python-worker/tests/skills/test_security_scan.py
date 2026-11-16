from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from shuliu_runtime.skills.lifecycle import SkillInstaller, SkillLifecycleError
from shuliu_runtime.skills.registry import SkillRegistry, SkillState
from shuliu_runtime.skills.security_scan import (
    SCAN_ENGINE_UNAVAILABLE,
    SCAN_TIMEOUT,
    ClamAvScanner,
    ScanEvidence,
    ScanStatus,
    SkillSecurityScanner,
)
from shuliu_runtime.skills.signature import PackageVerifier, checksum_files


class FakeSignatureVerifier:
    def verify(self, public_key: bytes, payload: bytes, signature: bytes) -> bool:
        return signature == b"test:" + hashlib.sha256(payload).digest()


class CleanFakeScanner:
    """Fake scanner used for lifecycle wiring tests, not for Release evidence."""

    engine = "fake"

    def __init__(self, status: ScanStatus = ScanStatus.CLEAN) -> None:
        self._status = status

    def scan(
        self, package_path: Path, *, expected_checksum: str | None, timeout: float
    ) -> ScanEvidence:
        digest = f"sha256:{checksum_files(package_path)}"
        if expected_checksum is not None and expected_checksum != digest:
            return ScanEvidence(
                status=ScanStatus.ERROR,
                package_digest=digest,
                engine=self.engine,
                engine_version="0",
                ruleset_version="0",
                scanned_at=datetime.now(UTC),
                reason_code="SCAN_CHECKSUM_MISMATCH",
            )
        return ScanEvidence(
            status=self._status,
            package_digest=digest,
            engine=self.engine,
            engine_version="0.0.0",
            ruleset_version="test-ruleset",
            scanned_at=datetime.now(UTC),
            reason_code="",
        )


class _Usage:
    def __init__(self) -> None:
        self.active_versions: set[tuple[str, str]] = set()

    def is_in_use(self, skill_id: str, version: str) -> bool:
        return (skill_id, version) in self.active_versions


def _package(
    root: Path,
    version: str,
    body: str,
    *,
    skill_id: str = "echo",
    permissions: list[str] | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(body, encoding="utf-8")
    manifest: dict[str, object] = {
        "id": skill_id,
        "version": version,
        "publisher": "shuliu-official",
        "signature": "",
        "checksum": f"sha256:{checksum_files(root, (root / 'README.md',))}",
        "requires": {"foundation": ">=0.1.0 <2.0.0", "skill_protocol": 1},
        "capabilities": ["text.echo"],
        "permissions": permissions or ["foundation.echo"],
        "dependencies": {},
    }
    payload = {name: value for name, value in manifest.items() if name != "signature"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = b"test:" + hashlib.sha256(canonical).digest()
    manifest["signature"] = base64.b64encode(signature).decode("ascii")
    (root / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    return root


def _installer(
    tmp_path: Path,
    *,
    scanner: SkillSecurityScanner | None = None,
    contract_test=None,
):
    registry = SkillRegistry(tmp_path / "registry")
    verifier = PackageVerifier(
        trusted_publishers={"shuliu-official": b"trusted-key"},
        foundation_version="0.1.0",
        skill_protocol=1,
        signature_verifier=FakeSignatureVerifier(),
    )
    installer = SkillInstaller(
        registry=registry,
        verifier=verifier,
        contract_test=contract_test or (lambda manifest: True),
        usage=_Usage(),
        scanner=scanner if scanner is not None else CleanFakeScanner(),
    )
    return installer, registry


# ---------------------------------------------------------------------------
# Unit: ClamAvScanner adapter behaviour (engine invoked through subprocess).
# ---------------------------------------------------------------------------


def _patch_engine(monkeypatch: pytest.MonkeyPatch, *, returncode: int, stdout: str = ""):
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        command = list(command)
        calls.append(command)
        if "--version" in command:
            result = subprocess.CompletedProcess(
                command,
                0,
                stdout="ClamAV 1.5.4/28173/Tue Nov  3 14:27:52 2026",
                stderr="",
            )
            return result
        if returncode == "timeout":
            raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout"))
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr("shuliu_runtime.skills.security_scan.subprocess.run", fake_run)
    return calls


def _version_probe_calls(calls: list[list[str]]) -> list[list[str]]:
    return [call for call in calls if "--version" not in call]


def test_clean_package_produces_verifiable_evidence(tmp_path, monkeypatch):
    package = _package(tmp_path / "pkg", "1.0.0", "clean body\n")
    calls = _patch_engine(monkeypatch, returncode=0)

    evidence = ClamAvScanner("clamscan").scan(
        package, expected_checksum=None, timeout=10.0
    )

    assert evidence.status is ScanStatus.CLEAN
    assert evidence.package_digest == f"sha256:{checksum_files(package)}"
    assert evidence.engine == "clamav"
    assert evidence.engine_version == "1.5.4"
    assert evidence.ruleset_version
    assert evidence.scanned_at is not None
    assert evidence.scanned_at.tzinfo is not None
    assert evidence.reason_code == ""
    # Only the version probe and the scan itself were executed.
    assert len(_version_probe_calls(calls)) == 1


def test_detected_verdict_is_returned_for_infected_packages(tmp_path, monkeypatch):
    package = _package(tmp_path / "pkg", "1.0.0", "infected body\n")
    _patch_engine(monkeypatch, returncode=1)

    evidence = ClamAvScanner("clamscan").scan(
        package, expected_checksum=None, timeout=10.0
    )

    assert evidence.status is ScanStatus.DETECTED
    assert evidence.package_digest == f"sha256:{checksum_files(package)}"


def test_scanner_timeout_fails_closed_without_a_verdict(tmp_path, monkeypatch):
    package = _package(tmp_path / "pkg", "1.0.0", "slow\n")
    _patch_engine(monkeypatch, returncode="timeout")

    evidence = ClamAvScanner("clamscan").scan(
        package, expected_checksum=None, timeout=0.5
    )

    assert evidence.status is ScanStatus.ERROR
    assert evidence.reason_code == SCAN_TIMEOUT


def test_missing_engine_binary_fails_closed(tmp_path, monkeypatch):
    package = _package(tmp_path / "pkg", "1.0.0", "body\n")

    def fake_run(command, **kwargs):
        raise FileNotFoundError("clamscan")

    monkeypatch.setattr("shuliu_runtime.skills.security_scan.subprocess.run", fake_run)

    evidence = ClamAvScanner("clamscan").scan(
        package, expected_checksum=None, timeout=10.0
    )

    assert evidence.status is ScanStatus.ERROR
    assert evidence.reason_code == SCAN_ENGINE_UNAVAILABLE


def test_engine_error_exit_code_fails_closed(tmp_path, monkeypatch):
    package = _package(tmp_path / "pkg", "1.0.0", "body\n")
    _patch_engine(monkeypatch, returncode=2)

    evidence = ClamAvScanner("clamscan").scan(
        package, expected_checksum=None, timeout=10.0
    )

    assert evidence.status is ScanStatus.ERROR
    assert evidence.reason_code != ""


def test_checksum_mismatch_refuses_to_run_the_engine(tmp_path, monkeypatch):
    package = _package(tmp_path / "pkg", "1.0.0", "body\n")
    calls = _patch_engine(monkeypatch, returncode=0)

    evidence = ClamAvScanner("clamscan").scan(
        package, expected_checksum="sha256:" + "0" * 64, timeout=10.0
    )

    assert evidence.status is ScanStatus.ERROR
    assert evidence.reason_code == "SCAN_CHECKSUM_MISMATCH"
    assert _version_probe_calls(calls) == []


def test_scan_evidence_never_carries_scan_output(tmp_path, monkeypatch):
    package = _package(tmp_path / "pkg", "1.0.0", "body\n")
    _patch_engine(monkeypatch, returncode=1, stdout="Win.Test.EICAR_HDB-1 FOUND")

    evidence = ClamAvScanner("clamscan").scan(
        package, expected_checksum=None, timeout=10.0
    )

    assert evidence.__dataclass_fields__.keys() == {
        "status",
        "package_digest",
        "engine",
        "engine_version",
        "ruleset_version",
        "scanned_at",
        "reason_code",
    }
    assert "FOUND" not in str(evidence)


@pytest.mark.parametrize("field", ["engine", "engine_version", "ruleset_version"])
def test_clean_evidence_without_engine_identification_is_invalid(tmp_path, field):
    values = {
        "engine": "clamav",
        "engine_version": "1.5.4",
        "ruleset_version": "27423",
    }
    values[field] = ""
    with pytest.raises(ValueError):
        ScanEvidence(
            status=ScanStatus.CLEAN,
            package_digest=f"sha256:{'a' * 64}",
            scanned_at=datetime.now(UTC),
            reason_code="",
            **values,
        )


# ---------------------------------------------------------------------------
# Lifecycle integration: the scan gates staging and activation.
# ---------------------------------------------------------------------------


def test_detected_package_cannot_enter_staging_or_activation(tmp_path):
    package = _package(tmp_path / "pkg", "1.0.0", "infected\n")
    installer, registry = _installer(
        tmp_path, scanner=CleanFakeScanner(status=ScanStatus.DETECTED)
    )

    with pytest.raises(SkillLifecycleError) as error:
        installer.install(package)

    assert error.value.code == "SKILL_SCAN_DETECTED"
    assert registry.installed_versions("echo") == ()
    assert registry.active_version("echo") is None


def test_scanner_error_blocks_the_install(tmp_path):
    package = _package(tmp_path / "pkg", "1.0.0", "body\n")

    class ErrorScanner(CleanFakeScanner):
        def scan(self, package_path, *, expected_checksum, timeout):
            return ScanEvidence(
                status=ScanStatus.ERROR,
                package_digest=f"sha256:{checksum_files(package_path)}",
                engine="fake",
                engine_version="0.0.0",
                ruleset_version="x",
                scanned_at=datetime.now(UTC),
                reason_code=SCAN_TIMEOUT,
            )

    installer, registry = _installer(tmp_path, scanner=ErrorScanner())

    with pytest.raises(SkillLifecycleError) as error:
        installer.install(package)

    assert error.value.code == "SKILL_SCAN_FAILED"
    assert registry.installed_versions("echo") == ()


def test_digest_mismatch_between_verdict_and_verified_package_blocks_install(tmp_path):
    package = _package(tmp_path / "pkg", "1.0.0", "body\n")

    class LyingScanner(CleanFakeScanner):
        def scan(self, package_path, *, expected_checksum, timeout):
            evidence = super().scan(
                package_path, expected_checksum=expected_checksum, timeout=timeout
            )
            return ScanEvidence(
                status=ScanStatus.CLEAN,
                package_digest=f"sha256:{'b' * 64}",
                engine=evidence.engine,
                engine_version=evidence.engine_version,
                ruleset_version=evidence.ruleset_version,
                scanned_at=evidence.scanned_at,
                reason_code="",
            )

    installer, registry = _installer(tmp_path, scanner=LyingScanner())

    with pytest.raises(SkillLifecycleError) as error:
        installer.install(package)

    assert error.value.code == "SKILL_SCAN_DIGEST_MISMATCH"
    assert registry.installed_versions("echo") == ()


def test_clean_verdict_without_engine_identification_cannot_install(tmp_path):
    package = _package(tmp_path / "pkg", "1.0.0", "body\n")

    class EvidenceFreeScanner(CleanFakeScanner):
        def scan(self, package_path, *, expected_checksum, timeout):
            evidence = super().scan(
                package_path, expected_checksum=expected_checksum, timeout=timeout
            )
            return ScanEvidence(
                status=ScanStatus.CLEAN,
                package_digest=evidence.package_digest,
                engine="",
                engine_version="",
                ruleset_version="",
                scanned_at=evidence.scanned_at,
                reason_code="",
            )

    installer, registry = _installer(tmp_path, scanner=EvidenceFreeScanner())

    with pytest.raises(SkillLifecycleError) as error:
        installer.install(package)

    assert error.value.code == "SKILL_SCAN_EVIDENCE_INVALID"
    assert registry.installed_versions("echo") == ()


def test_clean_scan_allows_install_and_carries_the_evidence(tmp_path):
    package = _package(tmp_path / "pkg", "1.0.0", "echo v1\n")
    installer, registry = _installer(tmp_path)

    result = installer.install(package)

    assert result.state is SkillState.ENABLED
    assert result.scan_evidence is not None
    assert result.scan_evidence.status is ScanStatus.CLEAN
    assert result.scan_evidence.package_digest == f"sha256:{checksum_files(package)}"
    assert registry.active_version("echo") == "1.0.0"
