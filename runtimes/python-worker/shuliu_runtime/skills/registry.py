from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import uuid
from enum import Enum
from pathlib import Path

from shuliu_runtime.skills.manifest import (
    ManifestError,
    SkillManifest,
    load_manifest,
    version_satisfies,
)


class SkillRegistryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SkillState(str, Enum):
    DISCOVERED = "DISCOVERED"
    DOWNLOADED = "DOWNLOADED"
    VERIFIED = "VERIFIED"
    STAGED = "STAGED"
    INSTALLED = "INSTALLED"
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    DEPRECATED = "DEPRECATED"
    REMOVED = "REMOVED"


_TRANSITIONS = {
    SkillState.DISCOVERED: {SkillState.DOWNLOADED},
    SkillState.DOWNLOADED: {SkillState.VERIFIED},
    SkillState.VERIFIED: {SkillState.STAGED},
    SkillState.STAGED: {SkillState.INSTALLED},
    SkillState.INSTALLED: {SkillState.ENABLED, SkillState.DEPRECATED, SkillState.REMOVED},
    SkillState.ENABLED: {SkillState.DISABLED, SkillState.DEPRECATED},
    SkillState.DISABLED: {SkillState.ENABLED, SkillState.DEPRECATED, SkillState.REMOVED},
    SkillState.DEPRECATED: {SkillState.REMOVED},
    SkillState.REMOVED: set(),
}


class SkillRegistry:
    """Filesystem registry with an atomically replaced state index and versioned packages."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise SkillRegistryError("SKILL_REGISTRY_INVALID", "Skill registry root cannot be a symlink")
        self._index_path = self.root / "registry.json"
        self._lock = threading.RLock()
        self._data = self._read_index()

    def observe(self, manifest: SkillManifest) -> None:
        with self._lock:
            entry = self._version_entry(manifest.skill_id, manifest.version)
            if entry is not None:
                if entry.get("checksum") != manifest.checksum:
                    raise SkillRegistryError(
                        "SKILL_VERSION_CONFLICT", "Skill version is already bound to different content"
                    )
                if entry.get("state") == SkillState.REMOVED.value:
                    raise SkillRegistryError("SKILL_VERSION_REMOVED", "Removed Skill version cannot be reinstalled")
                return
            skill = self._data["skills"].setdefault(
                manifest.skill_id, {"active_version": None, "versions": {}}
            )
            skill["versions"][manifest.version] = {
                "checksum": manifest.checksum,
                "state": SkillState.DISCOVERED.value,
                "history": [SkillState.DISCOVERED.value],
            }
            self._write_index()

    def transition(self, skill_id: str, version: str, state: SkillState) -> None:
        if not isinstance(state, SkillState):
            raise TypeError("Skill state is invalid")
        with self._lock:
            entry = self._version_entry(skill_id, version)
            if entry is None:
                raise SkillRegistryError("SKILL_VERSION_NOT_FOUND", "Skill version is not registered")
            current = SkillState(entry["state"])
            if state not in _TRANSITIONS[current]:
                raise SkillRegistryError(
                    "SKILL_STATE_TRANSITION_INVALID", "Skill lifecycle transition is not allowed"
                )
            entry["state"] = state.value
            entry["history"].append(state.value)
            self._write_index()

    def stage(self, source: str | Path, manifest: SkillManifest) -> Path:
        source_path = Path(source)
        stage_root = self.root / ".staging" / manifest.skill_id / manifest.version
        stage_path = stage_root / uuid.uuid4().hex
        stage_root.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(source_path, stage_path, symlinks=True)
        except OSError as error:
            self.abandon_staging(stage_path)
            raise SkillRegistryError("SKILL_STAGE_FAILED", "Skill package could not be staged") from error
        return stage_path

    def install_staged(self, stage_path: str | Path, manifest: SkillManifest) -> Path:
        stage = Path(stage_path)
        destination = self.package_path(manifest.skill_id, manifest.version)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise SkillRegistryError("SKILL_VERSION_CONFLICT", "Skill version is already installed")
        try:
            os.replace(stage, destination)
        except OSError as error:
            raise SkillRegistryError("SKILL_INSTALL_FAILED", "Staged Skill could not be installed") from error
        self._prune_staging_parents(stage)
        return destination

    def activate(self, skill_id: str, version: str) -> None:
        with self._lock:
            skill = self._data["skills"].get(skill_id)
            if skill is None:
                raise SkillRegistryError("SKILL_NOT_FOUND", "Skill is not registered")
            target = skill["versions"].get(version)
            if target is None:
                raise SkillRegistryError("SKILL_VERSION_NOT_FOUND", "Skill version is not registered")
            target_state = SkillState(target["state"])
            if target_state not in {SkillState.INSTALLED, SkillState.DISABLED, SkillState.ENABLED}:
                raise SkillRegistryError("SKILL_VERSION_NOT_INSTALLABLE", "Skill version cannot be enabled")
            previous = skill.get("active_version")
            if previous == version:
                return
            if previous is not None:
                old = skill["versions"].get(previous)
                if old is None or old["state"] != SkillState.ENABLED.value:
                    raise SkillRegistryError("SKILL_REGISTRY_CORRUPT", "Active Skill version state is inconsistent")
                self._set_state(old, SkillState.DISABLED)
            if target_state is not SkillState.ENABLED:
                self._set_state(target, SkillState.ENABLED)
            skill["active_version"] = version
            self._write_index()

    def disable(self, skill_id: str, version: str) -> None:
        with self._lock:
            skill = self._data["skills"].get(skill_id)
            entry = self._version_entry(skill_id, version)
            if skill is None or entry is None:
                raise SkillRegistryError("SKILL_VERSION_NOT_FOUND", "Skill version is not registered")
            if entry["state"] != SkillState.ENABLED.value:
                raise SkillRegistryError("SKILL_STATE_TRANSITION_INVALID", "Only an enabled Skill can be disabled")
            self._set_state(entry, SkillState.DISABLED)
            if skill.get("active_version") == version:
                skill["active_version"] = None
            self._write_index()

    def deprecate(self, skill_id: str, version: str) -> None:
        with self._lock:
            skill = self._data["skills"].get(skill_id)
            entry = self._version_entry(skill_id, version)
            if skill is None or entry is None:
                raise SkillRegistryError("SKILL_VERSION_NOT_FOUND", "Skill version is not registered")
            self._set_state(entry, SkillState.DEPRECATED)
            if skill.get("active_version") == version:
                skill["active_version"] = None
            self._write_index()

    def remove(self, skill_id: str, version: str) -> None:
        with self._lock:
            skill = self._data["skills"].get(skill_id)
            entry = self._version_entry(skill_id, version)
            if skill is None or entry is None:
                raise SkillRegistryError("SKILL_VERSION_NOT_FOUND", "Skill version is not registered")
            state = SkillState(entry["state"])
            if state not in {SkillState.INSTALLED, SkillState.DISABLED, SkillState.DEPRECATED}:
                raise SkillRegistryError("SKILL_STATE_TRANSITION_INVALID", "Skill version cannot be removed")
            self._set_state(entry, SkillState.REMOVED)
            if skill.get("active_version") == version:
                skill["active_version"] = None
            self._write_index()
            shutil.rmtree(self.package_path(skill_id, version), ignore_errors=True)

    def discard_uninstalled(self, skill_id: str, version: str) -> None:
        """Forget a failed install attempt only when no version directory was committed."""
        with self._lock:
            skill = self._data["skills"].get(skill_id)
            if skill is None or version not in skill["versions"]:
                return
            if self.package_path(skill_id, version).exists():
                raise SkillRegistryError(
                    "SKILL_VERSION_ALREADY_INSTALLED", "Installed Skill metadata cannot be discarded"
                )
            if skill.get("active_version") == version:
                raise SkillRegistryError(
                    "SKILL_VERSION_ACTIVE", "Active Skill metadata cannot be discarded"
                )
            del skill["versions"][version]
            if not skill["versions"]:
                del self._data["skills"][skill_id]
            self._write_index()

    def active_version(self, skill_id: str) -> str | None:
        with self._lock:
            skill = self._data["skills"].get(skill_id)
            return None if skill is None else skill.get("active_version")

    def installed_versions(self, skill_id: str) -> tuple[str, ...]:
        with self._lock:
            skill = self._data["skills"].get(skill_id)
            if skill is None:
                return ()
            return tuple(
                sorted(
                    version
                    for version, entry in skill["versions"].items()
                    if entry["state"] != SkillState.REMOVED.value
                    and self.package_path(skill_id, version).is_dir()
                )
            )

    def active_versions(self) -> dict[str, str]:
        with self._lock:
            return {
                skill_id: active
                for skill_id, skill in self._data["skills"].items()
                if (active := skill.get("active_version")) is not None
            }

    def active_dependents(self, skill_id: str, version: str) -> tuple[str, ...]:
        with self._lock:
            dependent_ids: list[str] = []
            for other_id, skill in self._data["skills"].items():
                active = skill.get("active_version")
                if other_id == skill_id or active is None:
                    continue
                manifest = self.manifest(other_id, active)
                requirement = manifest.dependencies.get(skill_id)
                if requirement is not None and version_satisfies(version, requirement):
                    dependent_ids.append(other_id)
            return tuple(sorted(dependent_ids))

    def state(self, skill_id: str, version: str) -> SkillState:
        with self._lock:
            entry = self._version_entry(skill_id, version)
            if entry is None:
                raise SkillRegistryError("SKILL_VERSION_NOT_FOUND", "Skill version is not registered")
            return SkillState(entry["state"])

    def history(self, skill_id: str, version: str) -> tuple[SkillState, ...]:
        with self._lock:
            entry = self._version_entry(skill_id, version)
            if entry is None:
                raise SkillRegistryError("SKILL_VERSION_NOT_FOUND", "Skill version is not registered")
            return tuple(SkillState(item) for item in entry["history"])

    def manifest(self, skill_id: str, version: str) -> SkillManifest:
        try:
            return load_manifest(self.package_path(skill_id, version) / "manifest.yaml")
        except ManifestError as error:
            raise SkillRegistryError("SKILL_MANIFEST_INVALID", "Installed Skill manifest is invalid") from error

    def package_path(self, skill_id: str, version: str) -> Path:
        return self.root / "skills" / skill_id / "versions" / version

    def abandon_staging(self, stage_path: str | Path) -> None:
        stage = Path(stage_path)
        staging_root = self.root / ".staging"
        if staging_root not in stage.parents:
            raise SkillRegistryError("SKILL_STAGE_INVALID", "Staging path is outside the registry")
        shutil.rmtree(stage, ignore_errors=True)
        self._prune_staging_parents(stage)

    def _prune_staging_parents(self, stage: Path) -> None:
        staging_root = self.root / ".staging"
        parent = stage.parent
        while parent != staging_root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
        try:
            staging_root.rmdir()
        except OSError:
            pass

    def _set_state(self, entry: dict, state: SkillState) -> None:
        current = SkillState(entry["state"])
        if state not in _TRANSITIONS[current]:
            raise SkillRegistryError(
                "SKILL_STATE_TRANSITION_INVALID", "Skill lifecycle transition is not allowed"
            )
        entry["state"] = state.value
        entry["history"].append(state.value)

    def _version_entry(self, skill_id: str, version: str) -> dict | None:
        skill = self._data["skills"].get(skill_id)
        return None if skill is None else skill["versions"].get(version)

    def _read_index(self) -> dict:
        if not self._index_path.exists():
            return {"schema_version": 1, "skills": {}}
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or data.get("schema_version") != 1
                or not isinstance(data.get("skills"), dict)
            ):
                raise ValueError("unsupported registry index")
            return data
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise SkillRegistryError("SKILL_REGISTRY_CORRUPT", "Skill registry index is invalid") from error

    def _write_index(self) -> None:
        encoded = json.dumps(self._data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".registry-", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(encoded)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_name, self._index_path)
        except OSError as error:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise SkillRegistryError("SKILL_REGISTRY_WRITE_FAILED", "Skill registry could not be persisted") from error
