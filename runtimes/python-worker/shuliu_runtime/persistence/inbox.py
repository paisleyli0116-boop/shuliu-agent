from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .outbox import canonical_json
from .sqlite import IdempotencyConflictError, SQLiteDatabase


class InboxRepository:
    def __init__(self, database: SQLiteDatabase):
        self._database = database

    def receive(
        self,
        event_id: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
    ) -> bool:
        if not event_id or not idempotency_key:
            raise ValueError("event_id and idempotency_key are required")
        payload_json = canonical_json(payload)

        with self._database.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM inbox WHERE event_id = ? OR idempotency_key = ?",
                (event_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["idempotency_key"] != idempotency_key
                    or existing["payload_json"] != payload_json
                ):
                    raise IdempotencyConflictError(
                        "event_id or idempotency_key is already bound to another payload"
                    )
                return False

            connection.execute(
                "INSERT INTO inbox (event_id, idempotency_key, payload_json, received_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    event_id,
                    idempotency_key,
                    payload_json,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return True
