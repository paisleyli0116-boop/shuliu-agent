from __future__ import annotations

import pytest

from shuliu_runtime.persistence.inbox import InboxRepository
from shuliu_runtime.persistence.outbox import (
    IdempotencyConflictError,
    InvalidServerAckError,
    OutboxRepository,
)
from shuliu_runtime.persistence.sqlite import SQLiteDatabase


def make_database(path):
    database = SQLiteDatabase(path)
    database.migrate()
    return database


@pytest.mark.migration
def test_outbox_ack_is_the_only_transition_to_synced(tmp_path):
    database_path = tmp_path / "foundation.sqlite3"
    database = make_database(database_path)
    outbox = OutboxRepository(database)

    outbox.enqueue("evt-1", "idem-1", {"kind": "foundation.event"})
    assert [record.event_id for record in outbox.pending()] == ["evt-1"]

    assert outbox.ack(
        "evt-1",
        {
            "event_id": "evt-1",
            "idempotency_key": "idem-1",
            "accepted": True,
        },
    ) is True
    assert outbox.pending() == []


@pytest.mark.migration
def test_lost_ack_keeps_event_pending_across_reopen(tmp_path):
    database_path = tmp_path / "foundation.sqlite3"
    database = make_database(database_path)
    OutboxRepository(database).enqueue("evt-1", "idem-1", {"kind": "event"})

    reopened = SQLiteDatabase(database_path)
    reopened.migrate()

    assert [record.event_id for record in OutboxRepository(reopened).pending()] == [
        "evt-1"
    ]


@pytest.mark.migration
def test_outbox_duplicate_enqueue_is_idempotent_but_conflicting_reuse_fails(tmp_path):
    outbox = OutboxRepository(make_database(tmp_path / "foundation.sqlite3"))

    first = outbox.enqueue("evt-1", "idem-1", {"value": 1})
    duplicate = outbox.enqueue("evt-1", "idem-1", {"value": 1})

    assert first.event_id == duplicate.event_id == "evt-1"
    with pytest.raises(IdempotencyConflictError):
        outbox.enqueue("evt-1", "idem-1", {"value": 2})


@pytest.mark.migration
def test_outbox_rejects_ack_for_another_event_or_idempotency_key(tmp_path):
    outbox = OutboxRepository(make_database(tmp_path / "foundation.sqlite3"))
    outbox.enqueue("evt-1", "idem-1", {"kind": "event"})

    with pytest.raises(InvalidServerAckError):
        outbox.ack(
            "evt-1",
            {
                "event_id": "evt-other",
                "idempotency_key": "idem-1",
                "accepted": True,
            },
        )

    assert [record.event_id for record in outbox.pending()] == ["evt-1"]


@pytest.mark.migration
def test_inbox_deduplicates_server_delivery_and_rejects_conflicting_reuse(tmp_path):
    inbox = InboxRepository(make_database(tmp_path / "foundation.sqlite3"))

    assert inbox.receive("evt-1", "idem-1", {"value": 1}) is True
    assert inbox.receive("evt-1", "idem-1", {"value": 1}) is False
    with pytest.raises(IdempotencyConflictError):
        inbox.receive("evt-1", "idem-1", {"value": 2})
