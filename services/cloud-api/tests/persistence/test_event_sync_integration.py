from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.persistence.postgres import PostgresPersistence
from shuliu_runtime.persistence.outbox import OutboxRepository
from shuliu_runtime.persistence.sqlite import SQLiteDatabase


SERVICE_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_outbox_resumes_after_lost_ack_and_server_deduplicates(tmp_path):
    local_database = SQLiteDatabase(tmp_path / "runtime.sqlite3")
    local_database.migrate()
    outbox = OutboxRepository(local_database)
    outbox.enqueue(
        "evt-1",
        "idem-1",
        {"organization_id": "org-1", "kind": "foundation.event"},
    )

    cloud_engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        migration_config = Config(str(SERVICE_ROOT / "alembic.ini"))
        with cloud_engine.connect() as connection:
            migration_config.attributes["connection"] = connection
            command.upgrade(migration_config, "head")
            connection.commit()

        cloud = PostgresPersistence(cloud_engine)
        first_attempt = outbox.pending()[0]
        first_ack = cloud.accept_event(
            organization_id="org-1",
            event_id=first_attempt.event_id,
            idempotency_key=first_attempt.idempotency_key,
            payload=first_attempt.payload,
        )
        # The server committed, but the response was lost before the client applied it.
        assert [item.event_id for item in outbox.pending()] == ["evt-1"]

        retry = outbox.pending()[0]
        retry_ack = cloud.accept_event(
            organization_id="org-1",
            event_id=retry.event_id,
            idempotency_key=retry.idempotency_key,
            payload=retry.payload,
        )
        assert first_ack.duplicate is False
        assert retry_ack.duplicate is True
        assert outbox.ack(retry.event_id, retry_ack.to_dict()) is True
        assert outbox.pending() == []

        with cloud_engine.connect() as connection:
            stored_count = connection.execute(
                text("SELECT COUNT(*) FROM events WHERE event_id = 'evt-1'")
            ).scalar_one()
        assert stored_count == 1
    finally:
        cloud_engine.dispose()
