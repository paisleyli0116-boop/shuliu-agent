from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from shuliu_runtime.skills.manifest import ManifestError, load_manifest
from shuliu_runtime.skills.signature import (
    Ed25519SignatureVerifier,
    PackageVerificationError,
    PackageVerifier,
    checksum_files,
)


class FakeSignatureVerifier:
    def verify(self, public_key: bytes, payload: bytes, signature: bytes) -> bool:
        return signature == b"test:" + hashlib.sha256(payload).digest()


def _signature(payload: bytes) -> str:
    return base64_encode(b"test:" + hashlib.sha256(payload).digest())


def base64_encode(value: bytes) -> str:
    import base64

    return base64.b64encode(value).decode("ascii")


def _signed_package(
    root: Path,
    *,
    version: str = "1.0.0",
    foundation_range: str = ">=0.1.0 <2.0.0",
    skill_protocol: int = 1,
    dependencies: dict[str, str] | None = None,
    permissions: list[str] | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("Business-neutral echo example.\n", encoding="utf-8")
    manifest: dict[str, object] = {
        "id": "echo",
        "version": version,
        "publisher": "shuliu-official",
        "signature": "",
        "checksum": f"sha256:{checksum_files(root, (root / 'README.md',))}",
        "requires": {
            "foundation": foundation_range,
            "skill_protocol": skill_protocol,
        },
        "capabilities": ["text.echo"],
        "permissions": permissions or ["foundation.echo"],
        "dependencies": dependencies or {},
    }
    signed_fields = {name: value for name, value in manifest.items() if name != "signature"}
    payload = json.dumps(signed_fields, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest["signature"] = _signature(payload)
    (root / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    return root


def test_verifies_trusted_publisher_signature_checksum_and_compatibility(tmp_path: Path):
    package = _signed_package(tmp_path / "echo")
    verifier = PackageVerifier(
        trusted_publishers={"shuliu-official": b"trusted-key"},
        foundation_version="0.1.0",
        skill_protocol=1,
        signature_verifier=FakeSignatureVerifier(),
    )

    manifest = verifier.verify(package, installed_skills={})

    assert manifest.skill_id == "echo"
    assert manifest.version == "1.0.0"
    assert manifest.publisher == "shuliu-official"


def test_rejects_unknown_publisher(tmp_path: Path):
    package = _signed_package(tmp_path / "echo")
    verifier = PackageVerifier(
        trusted_publishers={},
        foundation_version="0.1.0",
        skill_protocol=1,
        signature_verifier=FakeSignatureVerifier(),
    )

    with pytest.raises(PackageVerificationError) as failure:
        verifier.verify(package, installed_skills={})
    assert failure.value.code == "SKILL_PUBLISHER_UNTRUSTED"


def test_rejects_tampered_package_content(tmp_path: Path):
    package = _signed_package(tmp_path / "echo")
    (package / "README.md").write_text("tampered\n", encoding="utf-8")
    verifier = PackageVerifier(
        trusted_publishers={"shuliu-official": b"trusted-key"},
        foundation_version="0.1.0",
        skill_protocol=1,
        signature_verifier=FakeSignatureVerifier(),
    )

    with pytest.raises(PackageVerificationError) as failure:
        verifier.verify(package, installed_skills={})
    assert failure.value.code == "SKILL_CHECKSUM_INVALID"


def test_rejects_manifest_tampering_after_signature(tmp_path: Path):
    package = _signed_package(tmp_path / "echo")
    manifest = yaml.safe_load((package / "manifest.yaml").read_text(encoding="utf-8"))
    manifest["capabilities"] = ["text.echo", "browser.read"]
    (package / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    verifier = PackageVerifier(
        trusted_publishers={"shuliu-official": b"trusted-key"},
        foundation_version="0.1.0",
        skill_protocol=1,
        signature_verifier=FakeSignatureVerifier(),
    )

    with pytest.raises(PackageVerificationError) as failure:
        verifier.verify(package, installed_skills={})
    assert failure.value.code == "SKILL_SIGNATURE_INVALID"


@pytest.mark.parametrize(
    ("foundation_range", "skill_protocol", "expected"),
    [
        (">=2.0.0 <3.0.0", 1, "foundation"),
        (">=0.1.0 <2.0.0", 2, "protocol"),
    ],
)
def test_rejects_incompatible_foundation_or_skill_protocol(
    tmp_path: Path, foundation_range: str, skill_protocol: int, expected: str
):
    package = _signed_package(
        tmp_path / "echo",
        foundation_range=foundation_range,
        skill_protocol=skill_protocol,
    )
    verifier = PackageVerifier(
        trusted_publishers={"shuliu-official": b"trusted-key"},
        foundation_version="0.1.0",
        skill_protocol=1,
        signature_verifier=FakeSignatureVerifier(),
    )

    with pytest.raises(PackageVerificationError) as failure:
        verifier.verify(package, installed_skills={})
    assert expected in failure.value.code.lower()


def test_rejects_missing_or_incompatible_skill_dependency(tmp_path: Path):
    package = _signed_package(
        tmp_path / "echo", dependencies={"shared-parser": ">=2.0.0 <3.0.0"}
    )
    verifier = PackageVerifier(
        trusted_publishers={"shuliu-official": b"trusted-key"},
        foundation_version="0.1.0",
        skill_protocol=1,
        signature_verifier=FakeSignatureVerifier(),
    )

    with pytest.raises(PackageVerificationError) as failure:
        verifier.verify(package, installed_skills={"shared-parser": "1.4.0"})
    assert failure.value.code == "SKILL_DEPENDENCY_UNSATISFIED"


def test_manifest_rejects_unknown_fields_and_duplicate_yaml_keys():
    with pytest.raises(ManifestError):
        load_manifest(
            "id: echo\nversion: 1.0.0\npublisher: shuliu-official\n"
            "signature: x\nchecksum: sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
            "requires: {foundation: '>=0.1.0 <2.0.0', skill_protocol: 1}\n"
            "capabilities: [text.echo]\npermissions: [foundation.echo]\ndependencies: {}\n"
            "provider: arbitrary\n"
        )
    with pytest.raises(ManifestError):
        load_manifest("id: echo\nid: shadow\n")


def test_rejects_symlinks_in_package_tree(tmp_path: Path):
    package = _signed_package(tmp_path / "echo")
    outside = tmp_path / "outside.txt"
    outside.write_text("not part of the package", encoding="utf-8")
    (package / "external.txt").symlink_to(outside)
    verifier = PackageVerifier(
        trusted_publishers={"shuliu-official": b"trusted-key"},
        foundation_version="0.1.0",
        skill_protocol=1,
        signature_verifier=FakeSignatureVerifier(),
    )

    with pytest.raises(PackageVerificationError) as failure:
        verifier.verify(package, installed_skills={})
    assert failure.value.code == "SKILL_PACKAGE_SYMLINK"


def test_rejects_manifest_symlink_before_parsing_external_file(tmp_path: Path):
    package = _signed_package(tmp_path / "echo")
    manifest = package / "manifest.yaml"
    external = tmp_path / "external-manifest.yaml"
    manifest.replace(external)
    external.write_text("invalid: [manifest", encoding="utf-8")
    manifest.symlink_to(external)
    verifier = PackageVerifier(
        trusted_publishers={"shuliu-official": b"trusted-key"},
        foundation_version="0.1.0",
        skill_protocol=1,
        signature_verifier=FakeSignatureVerifier(),
    )

    with pytest.raises(PackageVerificationError) as failure:
        verifier.verify(package, installed_skills={})

    assert failure.value.code == "SKILL_PACKAGE_SYMLINK"


def test_checksum_uses_unambiguous_file_boundaries(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for root in (first, second):
        (root / "manifest.yaml").write_text("ignored by checksum\n", encoding="utf-8")
    (first / "a").write_bytes(b"xb\0y")
    (second / "a").write_bytes(b"x")
    (second / "b").write_bytes(b"y")

    assert checksum_files(first) != checksum_files(second)


def test_ed25519_verifier_accepts_rfc8032_test_vector():
    public_key = bytes.fromhex(
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    )
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )

    assert Ed25519SignatureVerifier().verify(public_key, b"", signature) is True
    assert Ed25519SignatureVerifier().verify(public_key, b"tampered", signature) is False
