from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from shuliu_runtime.skills.lifecycle import SkillInstaller, SkillLifecycleError
from shuliu_runtime.skills.registry import SkillRegistry, SkillState
from shuliu_runtime.skills.signature import PackageVerifier, checksum_files
from tests.fakes.fake_security_scan import FakeSkillScanner


class FakeSignatureVerifier:
    def verify(self, public_key: bytes, payload: bytes, signature: bytes) -> bool:
        return signature == b"test:" + hashlib.sha256(payload).digest()


def _package(
    root: Path,
    version: str,
    body: str,
    *,
    skill_id: str = "echo",
    dependencies: dict[str, str] | None = None,
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
        "dependencies": dependencies or {},
    }
    payload = {name: value for name, value in manifest.items() if name != "signature"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = b"test:" + hashlib.sha256(canonical).digest()
    import base64

    manifest["signature"] = base64.b64encode(signature).decode("ascii")
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return root


class Usage:
    def __init__(self):
        self.active_versions: set[tuple[str, str]] = set()

    def is_in_use(self, skill_id: str, version: str) -> bool:
        return (skill_id, version) in self.active_versions


def _installer(tmp_path: Path, *, contract_test=None, registry=None, usage=None):
    registry = registry or SkillRegistry(tmp_path / "registry")
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
        usage=usage or Usage(),
        scanner=FakeSkillScanner(),
    )
    return installer, registry


def test_install_records_lifecycle_and_persists_active_version_after_restart(tmp_path: Path):
    installer, registry = _installer(tmp_path)
    package = _package(tmp_path / "source-v1", "1.0.0", "echo v1\n")

    result = installer.install(package)
    reopened = SkillRegistry(tmp_path / "registry")

    assert result.skill_id == "echo"
    assert reopened.active_version("echo") == "1.0.0"
    assert reopened.history("echo", "1.0.0") == (
        SkillState.DISCOVERED,
        SkillState.DOWNLOADED,
        SkillState.VERIFIED,
        SkillState.STAGED,
        SkillState.INSTALLED,
        SkillState.ENABLED,
    )


def test_failed_contract_test_keeps_old_version_active_and_cleans_staging(tmp_path: Path):
    installer, registry = _installer(tmp_path)
    installer.install(_package(tmp_path / "source-v1", "1.0.0", "echo v1\n"))
    failing = _installer(
        tmp_path, contract_test=lambda manifest: False, registry=registry
    )[0]
    package = _package(tmp_path / "source-v2", "1.1.0", "echo v2\n")

    try:
        failing.install(package)
    except SkillLifecycleError as error:
        assert error.code == "SKILL_CONTRACT_TEST_FAILED"
    else:
        raise AssertionError("failed contract test must block activation")

    assert registry.active_version("echo") == "1.0.0"
    assert registry.installed_versions("echo") == ("1.0.0",)
    assert not tuple((tmp_path / "registry" / ".staging").glob("**/*"))


def test_upgrade_and_rollback_switch_atomic_active_version(tmp_path: Path):
    installer, registry = _installer(tmp_path)
    installer.install(_package(tmp_path / "source-v1", "1.0.0", "echo v1\n"))
    installer.install(_package(tmp_path / "source-v2", "1.1.0", "echo v2\n"))

    assert registry.active_version("echo") == "1.1.0"
    assert installer.rollback("echo", "1.0.0") == "1.0.0"
    assert registry.active_version("echo") == "1.0.0"
    assert registry.state("echo", "1.1.0") is SkillState.DISABLED


def test_active_run_prevents_skill_removal(tmp_path: Path):
    usage = Usage()
    installer, registry = _installer(tmp_path, usage=usage)
    installer.install(_package(tmp_path / "source-v1", "1.0.0", "echo v1\n"))
    usage.active_versions.add(("echo", "1.0.0"))

    try:
        installer.remove("echo", "1.0.0")
    except SkillLifecycleError as error:
        assert error.code == "SKILL_IN_USE"
    else:
        raise AssertionError("active SkillRun must prevent removal")

    assert registry.active_version("echo") == "1.0.0"


def test_active_skill_dependency_prevents_removing_required_version(tmp_path: Path):
    installer, registry = _installer(tmp_path)
    installer.install(
        _package(
            tmp_path / "parser-v1",
            "1.0.0",
            "shared parser contract\n",
            skill_id="shared-parser",
        )
    )
    installer.install(
        _package(
            tmp_path / "consumer-v1",
            "1.0.0",
            "consumer manifest\n",
            skill_id="consumer",
            dependencies={"shared-parser": ">=1.0.0 <2.0.0"},
        )
    )

    try:
        installer.remove("shared-parser", "1.0.0")
    except SkillLifecycleError as error:
        assert error.code == "SKILL_DEPENDENCY_IN_USE"
    else:
        raise AssertionError("enabled Skill dependency must prevent removal")

    assert registry.active_version("shared-parser") == "1.0.0"


def test_same_version_cannot_change_signed_manifest_metadata(tmp_path: Path):
    installer, registry = _installer(tmp_path)
    installer.install(_package(tmp_path / "source-original", "1.0.0", "echo v1\n"))
    changed = _package(
        tmp_path / "source-changed",
        "1.0.0",
        "echo v1\n",
        permissions=["foundation.echo", "browser.read"],
    )

    with pytest.raises(SkillLifecycleError) as failure:
        installer.install(changed)

    assert failure.value.code == "SKILL_VERSION_CONFLICT"
    assert registry.active_version("echo") == "1.0.0"
    assert registry.manifest("echo", "1.0.0").permissions == ("foundation.echo",)
