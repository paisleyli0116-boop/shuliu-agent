"""Trusted composition of the Browser and Computer runtimes.

Both runtimes existed only in tests before this module: nothing in production
constructed them, so their durable idempotency adapters, approval binding and
evidence persistence were never exercised by a real Sidecar. This is that
composition root.

Two properties are deliberately fixed here and must not become configurable:

* The Browser execution gate is `None`. Side-effecting browser operations stay
  refused until a durable capability has been verified; no environment variable
  can open the gate.
* A runtime that cannot be composed is reported by code and left absent. It is
  never replaced by a permissive fallback.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from shuliu_runtime.browser.adapter import BrowserRuntime
from shuliu_runtime.computer.adapter import ComputerRuntime
from shuliu_runtime.events.models import EventRecord, event_to_dict
from shuliu_runtime.persistence.outbox import OutboxRepository
from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.persistence.sqlite_idempotency import SQLiteIdempotencyStore
from shuliu_runtime.production.evidence import (
    EvidenceStoreUnavailable,
    FileEvidenceStore,
)
from shuliu_runtime.tools.registry import ToolRegistry

BROWSER_ORIGINS_ENVIRONMENT_KEY = "SHULIU_BROWSER_ALLOWED_ORIGINS"

BROWSER_ORIGINS_UNAVAILABLE = "BROWSER_ALLOWED_ORIGINS_UNAVAILABLE"
BROWSER_ORIGINS_INVALID = "BROWSER_ALLOWED_ORIGINS_INVALID"
BROWSER_RUNTIME_UNAVAILABLE = "BROWSER_RUNTIME_UNAVAILABLE"
COMPUTER_RUNTIME_UNSUPPORTED = "COMPUTER_RUNTIME_UNSUPPORTED"
COMPUTER_RUNTIME_UNAVAILABLE = "COMPUTER_RUNTIME_UNAVAILABLE"


@dataclass(frozen=True)
class DesktopRuntimeOutcome:
    """The desktop runtimes that could be trusted, plus why the rest could not."""

    browser: BrowserRuntime | None
    computer: ComputerRuntime | None
    browser_unavailable_code: str | None
    computer_unavailable_code: str | None

    @property
    def tool_providers(self) -> tuple[ToolRegistry, ...]:
        return tuple(
            runtime.registry
            for runtime in (self.browser, self.computer)
            if runtime is not None
        )


def compose_desktop_runtimes(
    *,
    database: SQLiteDatabase,
    environment: Mapping[str, str],
    data_directory: str | Path,
    identity,
    policy,
    approvals,
    browser_backend_factory: Callable[[], object] | None = None,
    computer_backend: object | None = None,
    platform: str | None = None,
) -> DesktopRuntimeOutcome:
    if not isinstance(database, SQLiteDatabase):
        raise TypeError("desktop composition requires SQLiteDatabase")
    root = Path(data_directory)
    outbox = OutboxRepository(database)

    def emit_tool_event(event: EventRecord) -> None:
        outbox.enqueue(
            event.event_id,
            f"desktop-tool-event:{event.event_id}",
            event_to_dict(event),
        )

    browser, browser_code = _compose_browser(
        database=database,
        environment=environment,
        root=root,
        identity=identity,
        policy=policy,
        approvals=approvals,
        event_sink=emit_tool_event,
        backend_factory=browser_backend_factory,
    )
    computer, computer_code = _compose_computer(
        database=database,
        root=root,
        identity=identity,
        policy=policy,
        approvals=approvals,
        event_sink=emit_tool_event,
        backend=computer_backend,
        platform=platform or os.name,
    )
    return DesktopRuntimeOutcome(browser, computer, browser_code, computer_code)


def _compose_browser(
    *,
    database: SQLiteDatabase,
    environment: Mapping[str, str],
    root: Path,
    identity,
    policy,
    approvals,
    event_sink,
    backend_factory,
) -> tuple[BrowserRuntime | None, str | None]:
    origins = parse_allowed_origins(environment.get(BROWSER_ORIGINS_ENVIRONMENT_KEY))
    if origins is None:
        return None, BROWSER_ORIGINS_UNAVAILABLE
    if not origins:
        return None, BROWSER_ORIGINS_INVALID
    try:
        profile_root = _ensure_directory(root / "browser-profiles")
        evidence = FileEvidenceStore(root / "evidence")
    except (OSError, EvidenceStoreUnavailable):
        return None, BROWSER_RUNTIME_UNAVAILABLE
    try:
        runtime = BrowserRuntime(
            identity=identity,
            policy=policy,
            approvals=approvals,
            idempotency_store=SQLiteIdempotencyStore(database),
            event_sink=event_sink,
            evidence_sink=evidence,
            profile_root=profile_root,
            allowed_origins=origins,
            backend_factory=backend_factory,
            # Fixed by design: no durable capability is verified, so every
            # side-effecting browser operation is refused before dispatch.
            execution_gate=None,
        )
    except Exception:
        return None, BROWSER_RUNTIME_UNAVAILABLE
    return runtime, None


def _compose_computer(
    *,
    database: SQLiteDatabase,
    root: Path,
    identity,
    policy,
    approvals,
    event_sink,
    backend,
    platform: str,
) -> tuple[ComputerRuntime | None, str | None]:
    if platform != "nt":
        return None, COMPUTER_RUNTIME_UNSUPPORTED
    try:
        evidence = FileEvidenceStore(root / "evidence")
        runtime = ComputerRuntime(
            identity=identity,
            policy=policy,
            approvals=approvals,
            idempotency_store=SQLiteIdempotencyStore(database),
            event_sink=event_sink,
            evidence_sink=evidence,
            backend=backend,
        )
    except Exception:
        return None, COMPUTER_RUNTIME_UNAVAILABLE
    return runtime, None


def parse_allowed_origins(raw: object) -> tuple[str, ...] | None:
    """Parse a comma-separated origin allowlist.

    Returns `None` when unset, an empty tuple when any entry is not a plain
    http(s) origin. Wildcards, paths, credentials, queries and fragments are
    refused: they cannot be compared safely against a request target.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    parsed: list[str] = []
    for entry in raw.split(","):
        candidate = entry.strip()
        if not candidate:
            return ()
        value = _origin(candidate)
        if value is None:
            return ()
        parsed.append(value)
    return tuple(dict.fromkeys(parsed))


def _origin(value: str) -> str | None:
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.username or parts.password:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return None
    if port is not None and not 0 < port < 65_536:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise OSError("not a directory")
    return path
