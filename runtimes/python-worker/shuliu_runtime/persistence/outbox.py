from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .sqlite import IdempotencyConflictError, SQLiteDatabase


class InvalidServerAckError(ValueError):
    """The acknowledgement does not match the queued event."""


class UnknownOutboxEventError(LookupError):
    """No pending or acknowledged event matches the requested id."""


@dataclass(frozen=True)
class OutboxRecord:
    event_id: str
    idempotency_key: str
    payload: dict[str, Any]
    created_at: str
    synced_at: str | None


def canonical_json(payload: Mapping[str, Any]) -> str:
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _record(row) -> OutboxRecord:
    return OutboxRecord(
        event_id=row["event_id"],
        idempotency_key=row["idempotency_key"],
        payload=json.loads(row["payload_json"]),
        created_at=row["created_at"],
        synced_at=row["synced_at"],
    )


class OutboxRepository:
    def __init__(self, database: SQLiteDatabase):
        self._database = database

    def enqueue(
        self,
        event_id: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
    ) -> OutboxRecord:
        if not event_id or not idempotency_key:
            raise ValueError("event_id and idempotency_key are required")
        payload_json = canonical_json(payload)

        with self._database.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM outbox WHERE event_id = ? OR idempotency_key = ?",
                (event_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["event_id"] != event_id
                    or existing["idempotency_key"] != idempotency_key
                    or existing["payload_json"] != payload_json
                ):
                    raise IdempotencyConflictError(
                        "event_id or idempotency_key is already bound to another payload"
                    )
                return _record(existing)

            created_at = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "INSERT INTO outbox "
                "(event_id, idempotency_key, payload_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (event_id, idempotency_key, payload_json, created_at),
            )
            row = connection.execute(
                "SELECT * FROM outbox WHERE event_id = ?", (event_id,)
            ).fetchone()
            return _record(row)

    def pending(self) -> list[OutboxRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM outbox WHERE synced_at IS NULL "
                "ORDER BY created_at, event_id"
            ).fetchall()
        return [_record(row) for row in rows]

    def ack(self, event_id: str, server_ack: Mapping[str, Any]) -> bool:
        if not isinstance(server_ack, Mapping):
            raise InvalidServerAckError("server_ack must be a mapping")
        if (
            server_ack.get("event_id") != event_id
            or not server_ack.get("idempotency_key")
            or server_ack.get("accepted") is not True
        ):
            raise InvalidServerAckError("server_ack does not confirm this event")

        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT idempotency_key, synced_at FROM outbox WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if row is None:
                raise UnknownOutboxEventError(event_id)
            if row["idempotency_key"] != server_ack["idempotency_key"]:
                raise InvalidServerAckError("server_ack idempotency key does not match")
            if row["synced_at"] is not None:
                return False

            connection.execute(
                "UPDATE outbox SET server_ack_json = ?, synced_at = ? WHERE event_id = ?",
                (
                    json.dumps(dict(server_ack), sort_keys=True, separators=(",", ":")),
                    datetime.now(timezone.utc).isoformat(),
                    event_id,
                ),
            )
        return True
