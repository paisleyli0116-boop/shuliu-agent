from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Protocol


_CONTENT_LABEL = re.compile(r"(prompt|body|content|message|user.?input)", re.IGNORECASE)


def _normalized_key(key: str) -> str:
    return re.sub(r"[-_\s]", "", key.lower())


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalized_key(key)
    if normalized == "secretref":
        return False
    return normalized == "token" or any(
        marker in normalized
        for marker in ("authorization", "cookie", "password", "secret", "apikey", "accesstoken", "refreshtoken")
    )


def redact(payload: object) -> object:
    if isinstance(payload, Mapping):
        return {
            key: redact(value)
            for key, value in payload.items()
            if not _is_sensitive_key(str(key))
        }
    if isinstance(payload, (list, tuple)):
        return [redact(value) for value in payload]
    return payload


def _validate_metadata(context: Mapping[str, object]) -> None:
    version = context.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("schema_version must be a positive integer")
    for field in ("timestamp", "organization_id", "trace_id"):
        value = context.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")


def validate_trace(spans: Sequence[Mapping[str, object]]) -> None:
    by_id: dict[str, Mapping[str, object]] = {}
    for span in spans:
        _validate_metadata(span)
        span_id = span.get("span_id")
        started_at = span.get("started_at")
        if not isinstance(span_id, str) or not span_id.strip():
            raise ValueError("span_id must be a non-empty string")
        if not isinstance(started_at, str) or not started_at.strip():
            raise ValueError("started_at must be a non-empty string")
        if span_id in by_id:
            raise ValueError(f"duplicate span_id: {span_id}")
        by_id[span_id] = span

    for span_id, span in by_id.items():
        parent_id = span.get("parent_span_id")
        if parent_id is None:
            continue
        parent = by_id.get(str(parent_id))
        if parent is None:
            raise ValueError(f"parent span not found: {parent_id}")
        if parent.get("trace_id") != span.get("trace_id") or parent.get("organization_id") != span.get("organization_id"):
            raise ValueError(f"parent span scope does not match: {parent_id}")

    for span_id, span in by_id.items():
        visited: set[str] = set()
        current_id: str | None = span_id
        while current_id is not None:
            if current_id in visited:
                raise ValueError(f"span parent cycle detected: {current_id}")
            visited.add(current_id)
            current = by_id[current_id]
            current_parent = current.get("parent_span_id")
            current_id = str(current_parent) if current_parent is not None else None


def create_log(fields: Mapping[str, object]) -> dict[str, object]:
    _validate_metadata(fields)
    for field in ("level", "component"):
        value = fields.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")

    allowed = {
        "schema_version",
        "timestamp",
        "organization_id",
        "trace_id",
        "level",
        "component",
        "run_id",
        "error_code",
        "retryable",
    }
    return {key: value for key, value in fields.items() if key in allowed}


def record_metric(
    name: str,
    value: int | float,
    labels: Mapping[str, str | int | float | bool],
    context: Mapping[str, object],
) -> dict[str, object]:
    _validate_metadata(context)
    if not name.strip():
        raise ValueError("metric name must be a non-empty string")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("metric value must be numeric")

    safe_labels: dict[str, str | int | float | bool] = {}
    for key, label in labels.items():
        if _CONTENT_LABEL.search(key):
            continue
        if isinstance(label, bool) or isinstance(label, (str, int)):
            safe_labels[key] = label
        elif isinstance(label, float) and isfinite(label):
            safe_labels[key] = label
        else:
            raise ValueError(f"metric label must be scalar: {key}")
    safe_labels = redact(safe_labels)
    return {
        "name": name,
        "value": value,
        "labels": safe_labels,
        "schema_version": context["schema_version"],
        "timestamp": context["timestamp"],
        "organization_id": context["organization_id"],
        "trace_id": context["trace_id"],
    }


class AuditRepository(Protocol):
    def append_audit(self, record: Mapping[str, object]) -> str: ...
