from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class MigrationError(RuntimeError):
    """Base error for local schema state that cannot be safely continued."""


class MigrationChecksumError(MigrationError):
    """An applied migration no longer matches its recorded checksum."""


class UnknownMigrationError(MigrationError):
    """The database contains a migration not known to this runtime."""


class IdempotencyConflictError(RuntimeError):
    """An idempotency key was reused for a different event payload."""


@dataclass(frozen=True)
class LocalMigration:
    migration_id: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        encoded = json.dumps(
            self.statements,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


LOCAL_MIGRATIONS = (
    LocalMigration(
        migration_id="0001_local_foundation",
        statements=(
            "CREATE TABLE cache_entries ("
            "cache_key TEXT PRIMARY KEY, value_json TEXT NOT NULL, "
            "expires_at TEXT)",
            "CREATE TABLE outbox ("
            "event_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, "
            "payload_json TEXT NOT NULL, created_at TEXT NOT NULL, "
            "server_ack_json TEXT, synced_at TEXT)",
            "CREATE INDEX outbox_pending_idx ON outbox(synced_at, created_at)",
            "CREATE TABLE inbox ("
            "event_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, "
            "payload_json TEXT NOT NULL, received_at TEXT NOT NULL)",
            "CREATE TABLE checkpoints ("
            "checkpoint_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, "
            "payload_json TEXT NOT NULL, schema_version INTEGER NOT NULL, "
            "updated_at TEXT NOT NULL)",
            "CREATE INDEX checkpoints_task_idx ON checkpoints(task_id, updated_at)",
            "CREATE TABLE local_task_state ("
            "task_id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at TEXT NOT NULL)",
        ),
    ),
)


class SQLiteDatabase:
    """SQLite store limited to cache and local delivery / recovery state."""

    def __init__(self, path: str | Path):
        self.path = str(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> tuple[str, ...]:
        applied_now: list[str] = []
        known = {migration.migration_id: migration for migration in LOCAL_MIGRATIONS}

        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "migration_id TEXT PRIMARY KEY, checksum TEXT NOT NULL, "
                "applied_at TEXT NOT NULL)"
            )
            rows = connection.execute(
                "SELECT migration_id, checksum FROM schema_migrations"
            ).fetchall()
            recorded = {row["migration_id"]: row["checksum"] for row in rows}

            unknown = set(recorded) - set(known)
            if unknown:
                raise UnknownMigrationError(
                    f"SQLite database contains unknown migration(s): {', '.join(sorted(unknown))}"
                )

            for migration_id, checksum in recorded.items():
                if checksum != known[migration_id].checksum:
                    raise MigrationChecksumError(
                        f"SQLite migration checksum mismatch: {migration_id}"
                    )

            for migration in LOCAL_MIGRATIONS:
                if migration.migration_id in recorded:
                    continue
                for statement in migration.statements:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations (migration_id, checksum, applied_at) "
                    "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                    (migration.migration_id, migration.checksum),
                )
                applied_now.append(migration.migration_id)

        return tuple(applied_now)
