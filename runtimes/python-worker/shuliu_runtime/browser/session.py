from __future__ import annotations

import math
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .verification import BrowserError


class BrowserBackend(Protocol):
    def request(self, operation: str, payload: dict, *, timeout: float, cancel_event=None) -> dict: ...
    def close(self) -> None: ...


@dataclass
class BrowserSession:
    session_id: str
    subject_id: str
    organization_id: str
    backend: BrowserBackend
    directory: tempfile.TemporaryDirectory
    expires_at: float
    observation: dict | None = None
    raw: dict | None = None
    elements: dict = field(default_factory=dict)
    quarantined: bool = False
    closed: bool = False


class SessionStore:
    def __init__(self, root: Path, factory, ttl: float):
        if isinstance(ttl, bool) or not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("session_ttl must be finite and positive")
        self.root, self.factory, self.ttl = Path(root).resolve(), factory, ttl
        self.root.mkdir(parents=True, exist_ok=True)
        self.sessions: dict[str, BrowserSession] = {}

    def create(self, principal) -> BrowserSession:
        directory = tempfile.TemporaryDirectory(prefix="shuliu-browser-", dir=self.root)
        try:
            session = BrowserSession(str(uuid4()), principal.subject_id, principal.organization_id,
                self.factory(), directory, time.monotonic() + self.ttl)
        except Exception:
            directory.cleanup()
            raise
        self.sessions[session.session_id] = session
        return session

    def get(self, session_id, principal, *, closing=False):
        session = self.sessions.get(session_id)
        if session is None:
            raise BrowserError("BROWSER_SESSION_NOT_FOUND")
        if (session.subject_id, session.organization_id) != (principal.subject_id, principal.organization_id):
            raise BrowserError("BROWSER_SESSION_FORBIDDEN")
        if not closing:
            if session.quarantined:
                raise BrowserError("BROWSER_RECONCILIATION_REQUIRED")
            if time.monotonic() >= session.expires_at:
                self.close(session)
                raise BrowserError("BROWSER_SESSION_EXPIRED")
            if session.closed:
                raise BrowserError("BROWSER_SESSION_CLOSED")
        return session

    def close(self, session):
        if session.closed:
            return
        # Only delete the generated profile underneath this store's trusted root.
        directory = Path(session.directory.name).resolve()
        directory.relative_to(self.root)
        session.backend.close()
        deadline = time.monotonic() + 5
        while True:
            try:
                if os.name == "nt":
                    extended = "\\\\?\\UNC\\" + str(directory)[2:] if str(directory).startswith("\\\\") else "\\\\?\\" + str(directory)
                    if Path(extended).exists():
                        shutil.rmtree(extended, onerror=_cleanup_error)
                session.directory.cleanup()
                break
            except OSError as error:
                # Windows can retain file handles briefly after Job termination.
                if os.name != "nt" or getattr(error, "winerror", None) not in (5, 32, 145):
                    raise
                if time.monotonic() >= deadline:
                    raise BrowserError("BROWSER_CLEANUP_FAILED") from None
                time.sleep(0.05)
        session.closed = True
        session.raw, session.observation, session.elements = None, None, {}

    def close_all(self):
        failures = []
        for session in self.sessions.values():
            try:
                self.close(session)
            except Exception as error:
                failures.append(error)
        if failures:
            # Retain failed sessions so the host can retry cleanup, never actions.
            raise BrowserError("BROWSER_CLEANUP_FAILED") from ExceptionGroup("Browser cleanup failures", failures)


def _cleanup_error(function, path, exception):
    # Temporary files may disappear as Windows finishes terminating descendants.
    if not isinstance(exception[1], FileNotFoundError):
        raise exception[1]
