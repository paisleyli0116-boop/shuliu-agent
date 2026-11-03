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


DATABASE_SCHEMA_VERSION = "1.3.0"


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
    LocalMigration(
        migration_id="0002_tool_idempotency",
        statements=(
            "CREATE TABLE tool_idempotency ("
            "organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128), "
            "tool_id TEXT NOT NULL CHECK (length(tool_id) BETWEEN 1 AND 128), "
            "operation_id TEXT NOT NULL CHECK (length(operation_id) BETWEEN 1 AND 128), "
            "idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256), "
            "request_digest TEXT NOT NULL CHECK (length(request_digest) BETWEEN 1 AND 128), "
            "status TEXT NOT NULL CHECK (status IN "
            "('CLAIMED', 'DISPATCHED', 'COMPLETED', 'INDETERMINATE')), "
            "result_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
            "CHECK ((status = 'COMPLETED' AND result_json IS NOT NULL) OR "
            "(status <> 'COMPLETED' AND result_json IS NULL)))",
            "CREATE UNIQUE INDEX uq_tool_idempotency_scope_key ON tool_idempotency "
            "(organization_id, tool_id, operation_id, idempotency_key)",
            "CREATE INDEX ix_tool_idempotency_status_updated "
            "ON tool_idempotency(status, updated_at)",
        ),
    ),
    LocalMigration(
        migration_id="0003_workflow_runs",
        statements=(
            "CREATE TABLE workflow_runs ("
            "run_id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK (revision >= 0), "
            "payload_json TEXT NOT NULL, updated_at TEXT NOT NULL)",
        ),
    ),
    LocalMigration(
        migration_id="0004_runtime_generation",
        statements=(
            "CREATE TABLE runtime_generation ("
            "runtime_id TEXT PRIMARY KEY, generation INTEGER NOT NULL CHECK (generation >= 1))",
        ),
    ),
    LocalMigration(
        migration_id="0005_runtime_generation_transition",
        statements=(
            "ALTER TABLE runtime_generation ADD COLUMN transition_id TEXT",
        ),
    ),
    LocalMigration(
        migration_id="0006_approval_requests",
        statements=(
            "CREATE TABLE approval_requests ("
            "approval_id TEXT PRIMARY KEY, "
            "organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128), "
            "requester_id TEXT NOT NULL, target_approver_id TEXT NOT NULL, "
            "action_id TEXT NOT NULL, action_summary TEXT NOT NULL, "
            "resource_json TEXT NOT NULL, "
            "risk TEXT NOT NULL CHECK (risk IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')), "
            "action_digest TEXT NOT NULL, policy_decision_json TEXT NOT NULL, "
            "trace_id TEXT NOT NULL, run_id TEXT, "
            "created_at TEXT NOT NULL, expires_at TEXT NOT NULL, "
            "status TEXT NOT NULL CHECK (status IN "
            "('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED', 'INVALIDATED', 'CONSUMED')), "
            "decision_by TEXT, decision_at TEXT)",
            "CREATE INDEX approval_requests_org_status_idx ON approval_requests"
            "(organization_id, status, created_at)",
            "CREATE TABLE approval_audit_events ("
            "event_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, "
            "event_type TEXT NOT NULL, organization_id TEXT NOT NULL, "
            "trace_id TEXT NOT NULL, occurred_at TEXT NOT NULL, payload_json TEXT NOT NULL)",
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
