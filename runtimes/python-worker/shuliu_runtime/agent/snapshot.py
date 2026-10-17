from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


class InvalidVersionSnapshot(ValueError):
    """Raised when a run cannot pin its resolved component versions."""


@dataclass(frozen=True)
class VersionSnapshot:
    agent_version: str
    entries: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not _non_empty(self.agent_version):
            raise InvalidVersionSnapshot("agent_version is required")
        if not isinstance(self.entries, tuple) or not self.entries:
            raise InvalidVersionSnapshot("resolved component versions are required")
        seen: set[str] = set()
        for entry in self.entries:
            if (
                not isinstance(entry, tuple)
                or len(entry) != 2
                or not _non_empty(entry[0])
                or not _non_empty(entry[1])
                or entry[0] in seen
            ):
                raise InvalidVersionSnapshot("resolved component versions are invalid")
            seen.add(entry[0])

    @classmethod
    def create(
        cls, agent_version: str, resolved_versions: Mapping[str, object]
    ) -> VersionSnapshot:
        if not _non_empty(agent_version):
            raise InvalidVersionSnapshot("agent_version is required")
        if not isinstance(resolved_versions, Mapping) or not resolved_versions:
            raise InvalidVersionSnapshot("resolved component versions are required")
        entries: list[tuple[str, str]] = []
        for name, version in resolved_versions.items():
            if not _non_empty(name) or not _non_empty(version):
                raise InvalidVersionSnapshot("resolved component versions are invalid")
            entries.append((name, version))
        return cls(agent_version=agent_version, entries=tuple(sorted(entries)))

    @property
    def resolved_versions(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self.entries))


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
