from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
import json
import re
from threading import RLock
from typing import Protocol

from shuliu_runtime.events.observability import redact


_TOOL_RESULT_FIELDS = {
    "tool_id",
    "tool_version",
    "execution_status",
    "verification_status",
    "output",
    "error_code",
    "policy_decision",
    "policy_version",
    "policy_rule_id",
}
_SAFE_RESULT_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
SAFE_RESULT_REFERENCE_SCHEMA_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}(?![\s\S])"
_SAFE_TRACE_OR_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
SAFE_TRACE_OR_SESSION_ID_SCHEMA_PATTERN = (
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}(?![\s\S])"
)
SAFE_ERROR_CODE_SCHEMA_PATTERN = r"^[A-Z][A-Z0-9_]{0,127}(?![\s\S])"
SAFE_BROWSER_ERROR_CODE_SCHEMA_PATTERN = r"^BROWSER_[A-Z_]{1,120}(?![\s\S])"
_SAFE_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_SAFE_STATUSES = frozenset(
    {"VERIFIED", "UNVERIFIED", "FAILED", "OBSERVED", "UNAVAILABLE", "READY", "SUCCEEDED"}
)
_SAFE_ACTION_KINDS = frozenset({"click", "type", "scroll", "navigate", "close"})
SAFE_IDEMPOTENCY_STATUSES = _SAFE_STATUSES
SAFE_IDEMPOTENCY_ACTION_KINDS = _SAFE_ACTION_KINDS
IDEMPOTENCY_RESULT_REFERENCE_FIELDS = frozenset({"result_ref", "evidence_ref"})
SAFE_IDEMPOTENCY_OUTPUT_FIELDS = IDEMPOTENCY_RESULT_REFERENCE_FIELDS | frozenset(
    {
        "before_evidence_ref",
        "status",
        "trace_id",
        "session_id",
        "retryable",
        "outcome_unknown",
        "error_code",
        "action_kind",
        "ok",
    }
)


class IdempotencyClaimStatus(str, Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
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

    def mark_dispatched(
        self, *, scope: tuple[str, ...], key: str, request_digest: str
    ) -> None: ...

    def mark_indeterminate(
        self, *, scope: tuple[str, ...], key: str, request_digest: str
    ) -> None: ...

    def release_before_dispatch(
        self, *, scope: tuple[str, ...], key: str, request_digest: str
    ) -> None: ...


@dataclass(frozen=True)
class _Record:
    request_digest: str
    status: str = "CLAIMED"
    result: object | None = None


class InMemoryIdempotencyStore:
    """Thread-safe process-local adapter intended for tests and short-lived runs."""

    def __init__(self) -> None:
        self._records: dict[tuple[tuple[str, ...], str], _Record] = {}
        self._lock = RLock()

    def begin(
        self, *, scope: tuple[str, ...], key: str, request_digest: str
    ) -> IdempotencyClaim:
        validate_idempotency_values(scope, key, request_digest)
        record_key = (scope, key)
        with self._lock:
            current = self._records.get(record_key)
            if current is None:
                self._records[record_key] = _Record(request_digest=request_digest)
                return IdempotencyClaim(IdempotencyClaimStatus.NEW)
            if current.request_digest != request_digest:
                return IdempotencyClaim(IdempotencyClaimStatus.CONFLICT)
            if current.status == "CLAIMED":
                return IdempotencyClaim(IdempotencyClaimStatus.IN_PROGRESS)
            if current.status in {"DISPATCHED", "INDETERMINATE"}:
                return IdempotencyClaim(
                    IdempotencyClaimStatus.RECONCILIATION_REQUIRED
                )
            return IdempotencyClaim(
                IdempotencyClaimStatus.COMPLETED,
                result=deepcopy(current.result),
            )

    def mark_dispatched(
        self, *, scope: tuple[str, ...], key: str, request_digest: str
    ) -> None:
        validate_idempotency_values(scope, key, request_digest)
        record_key = (scope, key)
        with self._lock:
            current = self._records.get(record_key)
            if current is None or current.request_digest != request_digest:
                raise ValueError("idempotency claim does not exist")
            if current.status == "DISPATCHED":
                return
            if current.status != "CLAIMED":
                raise ValueError("idempotency claim is not dispatchable")
            self._records[record_key] = _Record(
                request_digest=request_digest, status="DISPATCHED"
            )

    def mark_indeterminate(
        self, *, scope: tuple[str, ...], key: str, request_digest: str
    ) -> None:
        validate_idempotency_values(scope, key, request_digest)
        record_key = (scope, key)
        with self._lock:
            current = self._records.get(record_key)
            if current is None or current.request_digest != request_digest:
                raise ValueError("idempotency claim does not exist")
            if current.status in {"INDETERMINATE", "COMPLETED"}:
                return
            if current.status not in {"CLAIMED", "DISPATCHED"}:
                raise ValueError("idempotency claim cannot become indeterminate")
            self._records[record_key] = _Record(
                request_digest=request_digest, status="INDETERMINATE"
            )

    def complete(
        self,
        *,
        scope: tuple[str, ...],
        key: str,
        request_digest: str,
        result: object,
    ) -> None:
        validate_idempotency_values(scope, key, request_digest)
        record_key = (scope, key)
        with self._lock:
            current = self._records.get(record_key)
            if current is None or current.request_digest != request_digest:
                raise ValueError("idempotency claim does not exist")
            if current.status == "COMPLETED":
                if current.result != result:
                    raise ValueError("idempotency result is already recorded")
                return
            if current.status != "DISPATCHED":
                raise ValueError("idempotency claim was not dispatched")
            self._records[record_key] = _Record(
                request_digest=request_digest,
                status="COMPLETED",
                result=deepcopy(result),
            )

    def release_before_dispatch(
        self, *, scope: tuple[str, ...], key: str, request_digest: str
    ) -> None:
        validate_idempotency_values(scope, key, request_digest)
        record_key = (scope, key)
        with self._lock:
            current = self._records.get(record_key)
            if (
                current is not None
                and current.request_digest == request_digest
                and current.status == "CLAIMED"
            ):
                self._records.pop(record_key, None)


def validate_idempotency_values(
    scope: tuple[str, ...],
    key: str,
    request_digest: str,
    *,
    expected_scope_size: int | None = None,
) -> None:
    """Enforce limits shared by adapters before backend-specific writes."""
    if (
        not isinstance(scope, tuple)
        or not scope
        or any(not isinstance(part, str) or not part.strip() for part in scope)
    ):
        raise ValueError("idempotency scope must contain non-empty text")
    if expected_scope_size is not None and len(scope) != expected_scope_size:
        raise ValueError("idempotency scope has an invalid number of fields")
    if any(len(part) > 128 for part in scope):
        raise ValueError("idempotency scope values must be at most 128 characters")
    if not isinstance(key, str) or not key.strip() or len(key) > 256:
        raise ValueError("idempotency key is required and must be at most 256 characters")
    if (
        not isinstance(request_digest, str)
        or not request_digest.strip()
        or len(request_digest) > 128
    ):
        raise ValueError("request digest is required and must be at most 128 characters")


def encode_idempotency_result(result: object) -> str:
    """Serialize result metadata and an opaque reference, never tool output data."""
    if is_dataclass(result) and not isinstance(result, type):
        value: object = asdict(result)
    else:
        value = result
    if not isinstance(value, Mapping):
        raise ValueError("idempotency result must be a referenced object")
    if set(value) == _TOOL_RESULT_FIELDS:
        output = value.get("output")
        if not isinstance(output, Mapping):
            raise ValueError("idempotency Tool result must contain a result reference")
        safe_output = sanitize_idempotency_tool_output(output)
        if safe_output is None:
            raise ValueError("idempotency Tool result reference is invalid")
        persisted: Mapping[str, object] = {
            **value,
            "output": safe_output,
        }
    else:
        reference = value.get("result_ref")
        if not _is_safe_result_reference(reference):
            raise ValueError("idempotency result reference is required")
        persisted = {"result_ref": reference}
    safe_value = redact(persisted)
    try:
        return json.dumps(
            safe_value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("idempotency result is not safe JSON") from error


def sanitize_idempotency_tool_output(
    output: Mapping[str, object],
) -> dict[str, object] | None:
    """Keep only stable non-content metadata needed to replay a Tool result."""
    safe: dict[str, object] = {}
    for name in ("result_ref", "evidence_ref", "before_evidence_ref"):
        value = output.get(name)
        if value is not None:
            if not _is_safe_result_reference(value):
                return None
            safe[name] = value
    if not (IDEMPOTENCY_RESULT_REFERENCE_FIELDS & safe.keys()):
        return None

    status = output.get("status")
    if isinstance(status, str) and status in _SAFE_STATUSES:
        safe["status"] = status
    trace_id = output.get("trace_id")
    if isinstance(trace_id, str) and _SAFE_TRACE_OR_SESSION_ID.fullmatch(trace_id):
        safe["trace_id"] = trace_id
    session_id = output.get("session_id")
    if session_id is None and "session_id" in output:
        safe["session_id"] = None
    elif isinstance(session_id, str) and _SAFE_TRACE_OR_SESSION_ID.fullmatch(session_id):
        safe["session_id"] = session_id
    for name in ("retryable", "outcome_unknown", "ok"):
        value = output.get(name)
        if isinstance(value, bool):
            safe[name] = value
    error_code = output.get("error_code")
    if isinstance(error_code, str) and _SAFE_ERROR_CODE.fullmatch(error_code):
        safe["error_code"] = error_code
    action_kind = output.get("action_kind")
    if isinstance(action_kind, str) and action_kind in _SAFE_ACTION_KINDS:
        safe["action_kind"] = action_kind
    return safe


def is_safe_idempotency_trace_id(value: object) -> bool:
    """Return whether a trace identifier survives the durable replay projection."""
    return isinstance(value, str) and _SAFE_TRACE_OR_SESSION_ID.fullmatch(value) is not None


def _is_safe_result_reference(value: object) -> bool:
    return isinstance(value, str) and _SAFE_RESULT_REFERENCE.fullmatch(value) is not None


def decode_idempotency_result(encoded: str) -> object:
    try:
        value = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise ValueError("stored idempotency result is invalid") from error
    if not isinstance(value, (dict, list)):
        raise ValueError("stored idempotency result must be an object or array")
    return value
