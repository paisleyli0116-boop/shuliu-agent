"""Durable evidence references for desktop Tool side effects.

Browser and Computer Tools emit a redacted record before and after every
dispatch. The record must outlive the process: an "evidence_ref" that only
points at an in-memory list cannot support later reconciliation or audit.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path

DEFAULT_MAX_RECORD_BYTES = 256 * 1024

# Matched against a key with separators removed, so `api_key`, `apiKey` and
# `API-KEY` are all recognised. Erring towards refusal is intentional: a
# misclassified field only loses evidence, a missed credential leaks it.
_SENSITIVE_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "credential",
    "authorization",
    "cookie",
    "privatekey",
)


class EvidenceStoreUnavailable(RuntimeError):
    def __init__(self, code: str = "EVIDENCE_STORE_UNAVAILABLE") -> None:
        super().__init__(code)
        self.code = code


class EvidenceRecordInvalid(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class FileEvidenceStore:
    """Content-addressed JSON records below one trusted directory.

    Writing the same record twice returns the same reference and does not
    create a second file. A failed write raises: a caller that cannot persist
    evidence must fail closed rather than report a reference that does not
    exist.
    """

    def __init__(
        self, root: str | Path, *, max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES
    ) -> None:
        if isinstance(max_record_bytes, bool) or max_record_bytes <= 0:
            raise ValueError("max_record_bytes must be positive")
        self._root = Path(root)
        self._max = max_record_bytes
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise EvidenceStoreUnavailable() from None
        if not self._root.is_dir():
            raise EvidenceStoreUnavailable()

    def __call__(self, record: Mapping[str, object]) -> str:
        if not isinstance(record, Mapping):
            raise EvidenceRecordInvalid("EVIDENCE_RECORD_INVALID")
        if _contains_sensitive_key(record):
            raise EvidenceRecordInvalid("EVIDENCE_RECORD_SENSITIVE")
        try:
            payload = json.dumps(
                dict(record),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise EvidenceRecordInvalid("EVIDENCE_RECORD_INVALID") from None
        if len(payload) > self._max:
            raise EvidenceRecordInvalid("EVIDENCE_RECORD_TOO_LARGE")

        digest = hashlib.sha256(payload).hexdigest()[:32]
        target = self._root / f"{digest}.json"
        if target.is_file():
            return f"evidence:{digest}"
        temporary = self._root / f".{digest}.{os.getpid()}.tmp"
        try:
            with open(temporary, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except OSError:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise EvidenceStoreUnavailable() from None
        return f"evidence:{digest}"


def _contains_sensitive_key(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                compact = key.casefold().replace("_", "").replace("-", "")
                if any(fragment in compact for fragment in _SENSITIVE_FRAGMENTS):
                    return True
            if _contains_sensitive_key(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_key(item) for item in value)
    return False
