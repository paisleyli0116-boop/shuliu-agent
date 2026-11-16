from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from shuliu_runtime.skills.lifecycle import SkillInstaller, SkillLifecycleError
from shuliu_runtime.skills.registry import SkillRegistry
from shuliu_runtime.skills.signature import PackageVerifier, checksum_files
from tests.fakes.fake_security_scan import FakeSkillScanner


class FakeSignatureVerifier:
    def verify(self, public_key: bytes, payload: bytes, signature: bytes) -> bool:
        return signature == b"test:" + hashlib.sha256(payload).digest()


def _package(root: Path, version: str, permissions: list[str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    content = "business-neutral skill example\n"
    (root / "README.md").write_text(content, encoding="utf-8")
    content_checksum = checksum_files(root, (root / "README.md",))
    manifest: dict[str, object] = {
        "id": "echo",
        "version": version,
        "publisher": "company-internal",
        "signature": "",
        "checksum": f"sha256:{content_checksum}",
        "requires": {"foundation": ">=0.1.0 <2.0.0", "skill_protocol": 1},
        "capabilities": ["text.echo"],
        "permissions": permissions,
        "dependencies": {},
    }
    payload = {name: value for name, value in manifest.items() if name != "signature"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = b"test:" + hashlib.sha256(canonical).digest()
    import base64

    manifest["signature"] = base64.b64encode(signature).decode("ascii")
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return root


class PermissionApprover:
    def __init__(self, approved: bool):
        self.approved = approved
        self.requests = []

    def is_approved(self, skill_id, from_version, to_version, added_permissions):
        self.requests.append((skill_id, from_version, to_version, added_permissions))
        return self.approved


class NoActiveRuns:
    def is_in_use(self, skill_id, version):
        return False


def _build_installer(tmp_path: Path, approver=None):
    registry = SkillRegistry(tmp_path / "registry")
    installer = SkillInstaller(
        registry=registry,
        verifier=PackageVerifier(
            trusted_publishers={"company-internal": b"trusted-key"},
            foundation_version="0.1.0",
            skill_protocol=1,
            signature_verifier=FakeSignatureVerifier(),
        ),
        contract_test=lambda manifest: True,
        permission_approver=approver,
        usage=NoActiveRuns(),
        scanner=FakeSkillScanner(),
    )
    return installer, registry


def test_added_permissions_require_explicit_approval_and_preserve_old_version(tmp_path: Path):
    installer, registry = _build_installer(tmp_path)
    installer.install(_package(tmp_path / "v1", "1.0.0", ["foundation.echo"]))
    approver = PermissionApprover(approved=False)
    installer.permission_approver = approver

    with pytest.raises(SkillLifecycleError) as blocked:
        installer.install(
            _package(tmp_path / "v2", "1.1.0", ["foundation.echo", "browser.read"])
        )

    assert blocked.value.code == "PERMISSION_CHANGE_DETECTED"
    assert approver.requests == [
        ("echo", "1.0.0", "1.1.0", ("browser.read",))
    ]
    assert registry.active_version("echo") == "1.0.0"
    assert registry.installed_versions("echo") == ("1.0.0",)


def test_permission_reduction_does_not_require_reapproval(tmp_path: Path):
    approver = PermissionApprover(approved=False)
    installer, registry = _build_installer(tmp_path, approver)
    installer.install(
        _package(tmp_path / "v1", "1.0.0", ["foundation.echo", "browser.read"])
    )

    installer.install(_package(tmp_path / "v2", "1.1.0", ["foundation.echo"]))

    assert registry.active_version("echo") == "1.1.0"
    assert approver.requests == []


def test_permission_increase_can_activate_after_explicit_approval(tmp_path: Path):
    approver = PermissionApprover(approved=True)
    installer, registry = _build_installer(tmp_path, approver)
    installer.install(_package(tmp_path / "v1", "1.0.0", ["foundation.echo"]))

    installer.install(
        _package(tmp_path / "v2", "1.1.0", ["foundation.echo", "browser.read"])
    )

    assert registry.active_version("echo") == "1.1.0"
    assert approver.requests == [
        ("echo", "1.0.0", "1.1.0", ("browser.read",))
    ]
