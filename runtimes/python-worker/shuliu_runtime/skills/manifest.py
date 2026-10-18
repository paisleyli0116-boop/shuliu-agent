from __future__ import annotations

import base64
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import yaml
from yaml.events import AliasEvent


class ManifestError(ValueError):
    """Raised when a Skill manifest is malformed or outside protocol v1."""


_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_SEMVER = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?\Z"
)
_CHECKSUM = re.compile(r"sha256:[0-9a-f]{64}\Z")
_NAME = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
_FIELDS = {
    "id",
    "version",
    "publisher",
    "signature",
    "checksum",
    "requires",
    "capabilities",
    "permissions",
    "dependencies",
}


@dataclass(frozen=True, slots=True)
class SkillManifest:
    skill_id: str
    version: str
    publisher: str
    signature: str
    checksum: str
    foundation_range: str
    skill_protocol: int
    capabilities: tuple[str, ...]
    permissions: tuple[str, ...]
    dependencies: Mapping[str, str]

    @property
    def signature_bytes(self) -> bytes:
        try:
            return base64.b64decode(self.signature, validate=True)
        except (ValueError, TypeError) as error:
            raise ManifestError("manifest signature is not valid base64") from error

    def signed_payload(self) -> bytes:
        import json

        payload = {
            "id": self.skill_id,
            "version": self.version,
            "publisher": self.publisher,
            "checksum": self.checksum,
            "requires": {
                "foundation": self.foundation_range,
                "skill_protocol": self.skill_protocol,
            },
            "capabilities": list(self.capabilities),
            "permissions": list(self.permissions),
            "dependencies": dict(sorted(self.dependencies.items())),
        }
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")


class _StrictSafeLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(AliasEvent):
            raise ManifestError("YAML aliases are not allowed in a Skill manifest")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        if not isinstance(node, yaml.MappingNode):
            raise ManifestError("manifest root must be a mapping")
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise ManifestError("manifest keys must be strings")
            if key in mapping:
                raise ManifestError("duplicate YAML key in Skill manifest")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def load_manifest(source: str | Path) -> SkillManifest:
    if isinstance(source, Path):
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise ManifestError("Skill manifest cannot be read") from error
    elif isinstance(source, str):
        text = source
    else:
        raise ManifestError("Skill manifest source must be text or a path")
    if not text or len(text.encode("utf-8")) > 64 * 1024:
        raise ManifestError("Skill manifest is empty or exceeds 64 KiB")
    try:
        raw = yaml.load(text, Loader=_StrictSafeLoader)
    except ManifestError:
        raise
    except yaml.YAMLError as error:
        raise ManifestError("Skill manifest YAML is invalid") from error
    if not isinstance(raw, Mapping):
        raise ManifestError("manifest root must be a mapping")
    if set(raw) != _FIELDS:
        raise ManifestError("manifest fields are missing or unsupported")

    skill_id = _identifier(raw["id"], "Skill ID")
    version = _version(raw["version"], "Skill version")
    publisher = _identifier(raw["publisher"], "publisher ID")
    signature = _text(raw["signature"], "signature")
    _decode_signature(signature)
    checksum = _text(raw["checksum"], "checksum")
    if not _CHECKSUM.fullmatch(checksum):
        raise ManifestError("Skill checksum must use sha256 and 64 lowercase hex digits")

    requirements = raw["requires"]
    if not isinstance(requirements, Mapping) or set(requirements) != {
        "foundation",
        "skill_protocol",
    }:
        raise ManifestError("Skill requires must define foundation and skill_protocol")
    foundation_range = _text(requirements["foundation"], "Foundation compatibility range")
    validate_version_range(foundation_range)
    protocol = requirements["skill_protocol"]
    if isinstance(protocol, bool) or not isinstance(protocol, int) or protocol < 1:
        raise ManifestError("Skill protocol version is invalid")

    capabilities = _names(raw["capabilities"], "capabilities")
    permissions = _names(raw["permissions"], "permissions", allow_empty=True)
    dependencies = raw["dependencies"]
    if not isinstance(dependencies, Mapping):
        raise ManifestError("Skill dependencies must be a mapping")
    normalized_dependencies: dict[str, str] = {}
    for dependency_id, version_range in dependencies.items():
        dependency_id = _identifier(dependency_id, "dependency ID")
        if dependency_id == skill_id:
            raise ManifestError("Skill cannot depend on itself")
        normalized_range = _text(version_range, "dependency version range")
        validate_version_range(normalized_range)
        normalized_dependencies[dependency_id] = normalized_range

    return SkillManifest(
        skill_id=skill_id,
        version=version,
        publisher=publisher,
        signature=signature,
        checksum=checksum,
        foundation_range=foundation_range,
        skill_protocol=protocol,
        capabilities=capabilities,
        permissions=permissions,
        dependencies=MappingProxyType(dict(sorted(normalized_dependencies.items()))),
    )


def validate_version_range(value: str) -> None:
    tokens = value.replace(",", " ").split()
    if not tokens:
        raise ManifestError("version range is empty")
    for token in tokens:
        match = re.fullmatch(r"(>=|<=|>|<|==|=)(.+)", token)
        if match is None:
            raise ManifestError("version range contains an unsupported comparator")
        _version(match.group(2), "version range bound")


def version_satisfies(version: str, version_range: str) -> bool:
    actual = _parse_version(version)
    for token in version_range.replace(",", " ").split():
        match = re.fullmatch(r"(>=|<=|>|<|==|=)(.+)", token)
        if match is None:
            return False
        operator, bound_text = match.groups()
        bound = _parse_version(bound_text)
        comparison = _compare_versions(actual, bound)
        if not {
            ">=": comparison >= 0,
            "<=": comparison <= 0,
            ">": comparison > 0,
            "<": comparison < 0,
            "==": comparison == 0,
            "=": comparison == 0,
        }[operator]:
            return False
    return True


def _parse_version(value: str) -> tuple[int, int, int, tuple[str, ...] | None]:
    if not isinstance(value, str):
        raise ManifestError("version must be text")
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise ManifestError("version must follow SemVer")
    major, minor, patch, prerelease = match.groups()
    return (
        int(major),
        int(minor),
        int(patch),
        tuple(prerelease.split(".")) if prerelease else None,
    )


def _compare_versions(left, right) -> int:
    if left[:3] != right[:3]:
        return (left[:3] > right[:3]) - (left[:3] < right[:3])
    left_pre, right_pre = left[3], right[3]
    if left_pre is None or right_pre is None:
        return 0 if left_pre is right_pre else (1 if left_pre is None else -1)
    for left_part, right_part in zip(left_pre, right_pre):
        if left_part == right_part:
            continue
        left_numeric, right_numeric = left_part.isdigit(), right_part.isdigit()
        if left_numeric and right_numeric:
            return (int(left_part) > int(right_part)) - (int(left_part) < int(right_part))
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return (left_part > right_part) - (left_part < right_part)
    return (len(left_pre) > len(right_pre)) - (len(left_pre) < len(right_pre))


def _identifier(value: object, field: str) -> str:
    text = _text(value, field)
    if not _ID.fullmatch(text):
        raise ManifestError(f"{field} is invalid")
    return text


def _version(value: object, field: str) -> str:
    text = _text(value, field)
    _parse_version(text)
    return text


def _names(value: object, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ManifestError(f"{field} must be a list")
    if any(not isinstance(item, str) or not _NAME.fullmatch(item) for item in value):
        raise ManifestError(f"{field} contains an invalid name")
    if len(set(value)) != len(value):
        raise ManifestError(f"{field} contains duplicates")
    return tuple(value)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field} is required")
    return value.strip()


def _decode_signature(signature: str) -> bytes:
    try:
        decoded = base64.b64decode(signature, validate=True)
    except (ValueError, TypeError) as error:
        raise ManifestError("manifest signature is not valid base64") from error
    if not decoded:
        raise ManifestError("manifest signature is empty")
    return decoded
