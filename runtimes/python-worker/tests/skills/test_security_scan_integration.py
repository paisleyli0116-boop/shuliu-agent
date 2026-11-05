"""Integration tests for the real ClamAV scanner engine.

The real-engine tests only execute when the engine is configured through
environment variables.  They are skipped - never silently passed - when the
engine is absent, because a missing engine is a fail-closed configuration and
cannot provide `skill_security_scan` Release evidence.

Environment:
- ``SHULIU_TEST_CLAMAV_SCAN_BINARY``: path to ``clamscan`` (or ``clamscan.exe``).
- ``SHULIU_TEST_CLAMAV_DATABASE_DIR``: ClamAV signature database directory.

The EICAR fixture is stored base64-encoded so the repository itself does not
contain a file that endpoint protection would quarantine on checkout; the
bytes written to the scan target at test time are the genuine standard EICAR
test string, which every antivirus engine must detect.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

import pytest
import yaml

from shuliu_runtime.skills.lifecycle import SkillInstaller, SkillLifecycleError
from shuliu_runtime.skills.registry import SkillRegistry
from shuliu_runtime.skills.security_scan import ClamAvScanner, ScanStatus
from shuliu_runtime.skills.signature import PackageVerifier, checksum_files
from tests.fakes.fake_security_scan import FakeSkillScanner

CLAMAV_BINARY_ENV = "SHULIU_TEST_CLAMAV_SCAN_BINARY"
CLAMAV_DATABASE_ENV = "SHULIU_TEST_CLAMAV_DATABASE_DIR"

ENGINE_CONFIGURED = bool(os.environ.get(CLAMAV_BINARY_ENV))


class FakeSignatureVerifier:
    def verify(self, public_key: bytes, payload: bytes, signature: bytes) -> bool:
        return signature == b"test:" + hashlib.sha256(payload).digest()


class _Usage:
    def is_in_use(self, skill_id: str, version: str) -> bool:
        return False


def _package_with_files(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    hashed = tuple(sorted(root / name for name in files))
    manifest: dict[str, object] = {
        "id": "echo",
        "version": "1.0.0",
        "publisher": "shuliu-official",
        "signature": "",
        "checksum": f"sha256:{checksum_files(root, hashed)}",
        "requires": {"foundation": ">=0.1.0 <2.0.0", "skill_protocol": 1},
        "capabilities": ["text.echo"],
        "permissions": ["foundation.echo"],
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


def _clamav_scanner() -> ClamAvScanner:
    return ClamAvScanner(
        os.environ[CLAMAV_BINARY_ENV],
        database_dir=os.environ.get(CLAMAV_DATABASE_ENV),
    )


def _installer(tmp_path: Path, scanner) -> tuple[SkillInstaller, SkillRegistry]:
    registry = SkillRegistry(tmp_path / "registry")
    installer = SkillInstaller(
        registry=registry,
        verifier=PackageVerifier(
            trusted_publishers={"shuliu-official": b"trusted-key"},
            foundation_version="0.1.0",
            skill_protocol=1,
            signature_verifier=FakeSignatureVerifier(),
        ),
        contract_test=lambda manifest: True,
        usage=_Usage(),
        scanner=scanner,
    )
    return installer, registry


def test_a_missing_engine_fails_closed_and_blocks_the_install(tmp_path: Path) -> None:
    """Without a configured engine, installation must fail closed, not skip the scan."""
    package = _package_with_files(tmp_path / "pkg", {"README.md": "echo v1\n"})
    unavailable = ClamAvScanner(tmp_path / "no-such-engine" / "clamscan.exe")
    installer, registry = _installer(tmp_path, unavailable)

    with pytest.raises(SkillLifecycleError) as error:
        installer.install(package)

    assert error.value.code == "SKILL_SCAN_FAILED"
    assert registry.installed_versions("echo") == ()
    assert registry.active_version("echo") is None


@pytest.mark.skipif(
    not ENGINE_CONFIGURED,
    reason="real scanner engine not configured on this environment; fail-closed is covered separately",
)
def test_a_clean_package_scans_clean_with_the_real_engine(tmp_path: Path) -> None:
    package = _package_with_files(tmp_path / "pkg", {"README.md": "echo v1\n"})
    installer, registry = _installer(tmp_path, _clamav_scanner())

    result = installer.install(package)

    assert result.scan_evidence is not None
    assert result.scan_evidence.status is ScanStatus.CLEAN
    assert result.scan_evidence.engine == "clamav"
    assert result.scan_evidence.package_digest == f"sha256:{checksum_files(package)}"
    assert registry.active_version("echo") == "1.0.0"


@pytest.mark.skipif(
    not ENGINE_CONFIGURED,
    reason="real scanner engine not configured on this environment; fail-closed is covered separately",
)
def test_the_real_engine_detects_the_eicar_test_file(tmp_path: Path) -> None:
    eicar = base64.b64decode(
        (Path(__file__).parents[1] / "fixtures" / "skills" / "eicar.com")
        .read_text(encoding="utf-8")
        .strip()
    ).decode("ascii")
    assert len(eicar) == 68
    package = _package_with_files(
        tmp_path / "pkg", {"README.md": "benign\n", "eicar.com": eicar}
    )
    installer, registry = _installer(tmp_path, _clamav_scanner())

    with pytest.raises(SkillLifecycleError) as error:
        installer.install(package)

    assert error.value.code == "SKILL_SCAN_DETECTED"
    assert registry.installed_versions("echo") == ()
    assert registry.active_version("echo") is None
