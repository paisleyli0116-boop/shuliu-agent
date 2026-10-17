from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Protocol


class IdempotencyClaimStatus(str, Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class IdempotencyClaim:
    status: IdempotencyClaimStatus
    result: object | None = None


class IdempotencyStore(Protocol):
    def begin(
        self, *, scope: tuple[str, ...], key: str, request_digest: str
    ) -> IdempotencyClaim: ...

    def complete(
        self,
        *,
        scope: tuple[str, ...],
        key: str,
        request_digest: str,
        result: object,
    ) -> None: ...

    def release(
        self, *, scope: tuple[str, ...], key: str, request_digest: str
    ) -> None: ...


@dataclass(frozen=True)
class _Record:
    request_digest: str
    result: object | None = None


class InMemoryIdempotencyStore:
    """Thread-safe process-local adapter intended for tests and short-lived runs."""

    def __init__(self) -> None:
        self._records: dict[tuple[tuple[str, ...], str], _Record] = {}
        self._lock = RLock()

    def begin(
        self, *, scope: tuple[str, ...], key: str, request_digest: str
    ) -> IdempotencyClaim:
        _validate_key(scope, key, request_digest)
        record_key = (scope, key)
        with self._lock:
            current = self._records.get(record_key)
            if current is None:
                self._records[record_key] = _Record(request_digest=request_digest)
                return IdempotencyClaim(IdempotencyClaimStatus.NEW)
            if current.request_digest != request_digest:
                return IdempotencyClaim(IdempotencyClaimStatus.CONFLICT)
            if current.result is None:
                return IdempotencyClaim(IdempotencyClaimStatus.IN_PROGRESS)
            return IdempotencyClaim(
                IdempotencyClaimStatus.COMPLETED,
                result=deepcopy(current.result),
            )

    def complete(
        self,
        *,
        scope: tuple[str, ...],
        key: str,
        request_digest: str,
        result: object,
    ) -> None:
        _validate_key(scope, key, request_digest)
        record_key = (scope, key)
        with self._lock:
            current = self._records.get(record_key)
            if current is None or current.request_digest != request_digest:
                raise ValueError("idempotency claim does not exist")
            if current.result is not None:
                raise ValueError("idempotency claim is already complete")
            self._records[record_key] = _Record(
                request_digest=request_digest,
                result=deepcopy(result),
            )

    def release(self, *, scope: tuple[str, ...], key: str, request_digest: str) -> None:
        _validate_key(scope, key, request_digest)
        record_key = (scope, key)
        with self._lock:
            current = self._records.get(record_key)
            if (
                current is not None
                and current.request_digest == request_digest
                and current.result is None
            ):
                self._records.pop(record_key, None)


def _validate_key(scope: tuple[str, ...], key: str, request_digest: str) -> None:
    if (
        not isinstance(scope, tuple)
        or not scope
        or any(not isinstance(part, str) or not part.strip() for part in scope)
    ):
        raise ValueError("idempotency scope must contain non-empty text")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("idempotency key is required")
    if not isinstance(request_digest, str) or not request_digest.strip():
        raise ValueError("request digest is required")
