from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from .observability import redact


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    schema_version: int
    timestamp: str
    organization_id: str
    trace_id: str
    event_type: str
    payload: dict[str, object]


def _required_text(data: Mapping[str, object], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _validate_timestamp(value: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must be a valid date-time string") from error


def event_from_dict(data: Mapping[str, object]) -> EventRecord:
    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("schema_version must be a positive integer")

    timestamp = _required_text(data, "timestamp")
    _validate_timestamp(timestamp)
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be an object")
    safe_payload = redact(dict(payload))
    if not isinstance(safe_payload, dict):
        raise ValueError("event payload must be an object")

    return EventRecord(
        event_id=_required_text(data, "event_id"),
        schema_version=version,
        timestamp=timestamp,
        organization_id=_required_text(data, "organization_id"),
        trace_id=_required_text(data, "trace_id"),
        event_type=_required_text(data, "event_type"),
        payload=safe_payload,
    )


def event_to_dict(event: EventRecord | Mapping[str, object]) -> dict[str, object]:
    record = event if isinstance(event, EventRecord) else event_from_dict(event)
    safe_payload = redact(record.payload)
    if not isinstance(safe_payload, dict):
        raise ValueError("event payload must be an object")
    return {
        "event_id": record.event_id,
        "schema_version": record.schema_version,
        "timestamp": record.timestamp,
        "organization_id": record.organization_id,
        "trace_id": record.trace_id,
        "event_type": record.event_type,
        "payload": safe_payload,
    }
