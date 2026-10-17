import pytest

from shuliu_runtime.events.models import EventRecord, event_from_dict, event_to_dict


def test_event_model_round_trips_versioned_event() -> None:
    event = {
        "event_id": "evt-01",
        "schema_version": 1,
        "timestamp": "2026-10-17T08:00:00.000Z",
        "organization_id": "org-01",
        "trace_id": "trace-01",
        "event_type": "task.started",
        "payload": {"task_id": "task-01"},
    }

    assert event_to_dict(event_from_dict(event)) == event


def test_event_model_rejects_missing_schema_version() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        event_from_dict(
            {
                "event_id": "evt-01",
                "timestamp": "2026-10-17T08:00:00.000Z",
                "organization_id": "org-01",
                "trace_id": "trace-01",
                "event_type": "task.started",
                "payload": {},
            }
        )


def test_event_model_removes_credentials_from_payload() -> None:
    event = {
        "event_id": "evt-02",
        "schema_version": 1,
        "timestamp": "2026-10-17T08:00:00.000Z",
        "organization_id": "org-01",
        "trace_id": "trace-01",
        "event_type": "task.started",
        "payload": {
            "task_id": "task-01",
            "authorization": "Bearer private-token",
        },
    }

    serialized = str(event_to_dict(event_from_dict(event)))
    assert "private-token" not in serialized
    assert "task-01" in serialized


def test_event_serializer_sanitizes_directly_constructed_records() -> None:
    event = EventRecord(
        event_id="evt-03",
        schema_version=1,
        timestamp="2026-10-17T08:00:00.000Z",
        organization_id="org-01",
        trace_id="trace-01",
        event_type="task.started",
        payload={"authorization": "Bearer direct-secret", "task_id": "task-01"},
    )

    serialized = str(event_to_dict(event))
    assert "direct-secret" not in serialized
    assert "task-01" in serialized
