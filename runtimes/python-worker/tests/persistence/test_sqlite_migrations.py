from __future__ import annotations

import sqlite3

import pytest

from shuliu_runtime.persistence.sqlite import (
    LOCAL_MIGRATIONS,
    MigrationChecksumError,
    SQLiteDatabase,
    UnknownMigrationError,
)


@pytest.mark.migration
def test_sqlite_migrations_create_only_approved_local_tables(tmp_path):
    database_path = tmp_path / "foundation.sqlite3"
    database = SQLiteDatabase(database_path)

    database.migrate()

    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        migration_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0]

    assert {
        "schema_migrations",
        "cache_entries",
        "outbox",
        "inbox",
        "checkpoints",
        "local_task_state",
        "tool_idempotency",
    } <= tables
    assert not {
        "tasks",
        "agent_runs",
        "events",
        "approvals",
        "workers",
        "worker_leases",
        "knowledge_documents",
    } & tables
    assert migration_count == len(LOCAL_MIGRATIONS)


@pytest.mark.migration
def test_sqlite_tool_idempotency_scope_is_unique_and_state_is_constrained(tmp_path):
    database = SQLiteDatabase(tmp_path / "foundation.sqlite3")
    database.migrate()

    with database.connect() as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(tool_idempotency)")
        }
        unique_indexes = [
            row["name"]
            for row in connection.execute("PRAGMA index_list(tool_idempotency)")
            if row["unique"]
        ]
        scope_columns = [
            row["name"]
            for row in connection.execute(
                "PRAGMA index_info(uq_tool_idempotency_scope_key)"
            )
        ]

    assert {
        "organization_id",
        "tool_id",
        "operation_id",
        "idempotency_key",
        "request_digest",
        "status",
        "result_json",
        "created_at",
        "updated_at",
    } <= columns
    assert "uq_tool_idempotency_scope_key" in unique_indexes
    assert scope_columns == [
        "organization_id",
        "tool_id",
        "operation_id",
        "idempotency_key",
    ]


@pytest.mark.migration
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("organization_id", "o" * 129),
        ("tool_id", "t" * 129),
        ("operation_id", "p" * 129),
        ("idempotency_key", "k" * 257),
        ("request_digest", "d" * 129),
    ],
)
def test_sqlite_tool_idempotency_schema_rejects_unpersistable_values(
    tmp_path, field, value
):
    database = SQLiteDatabase(tmp_path / "foundation.sqlite3")
    database.migrate()
    base = {
        "organization_id": "org-1",
        "tool_id": "tool-1",
        "operation_id": "operation-1",
        "idempotency_key": "key-1",
        "request_digest": "digest-1",
        "status": "CLAIMED",
        "result_json": None,
        "created_at": "2026-10-26T00:00:00Z",
        "updated_at": "2026-10-26T00:00:00Z",
    }
    base[field] = value

    with pytest.raises(sqlite3.IntegrityError):
        with database.connect() as connection:
            connection.execute(
                "INSERT INTO tool_idempotency "
                "(organization_id, tool_id, operation_id, "
                "idempotency_key, request_digest, status, result_json, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(base.values()),
            )


@pytest.mark.migration
def test_sqlite_migration_is_repeatable_and_records_checksum(tmp_path):
    database = SQLiteDatabase(tmp_path / "foundation.sqlite3")

    database.migrate()
    with database.connect() as connection:
        first_record = connection.execute(
            "SELECT migration_id, checksum, applied_at FROM schema_migrations"
        ).fetchone()

    database.migrate()
    with database.connect() as connection:
        second_record = connection.execute(
            "SELECT migration_id, checksum, applied_at FROM schema_migrations"
        ).fetchone()

    assert first_record == second_record
    assert first_record[0]
    assert len(first_record[1]) == 64
    assert first_record[2]


@pytest.mark.migration
def test_modified_applied_sqlite_migration_blocks_startup(tmp_path):
    database_path = tmp_path / "foundation.sqlite3"
    database = SQLiteDatabase(database_path)
    database.migrate()

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum = ?",
            ("0" * 64,),
        )

    with pytest.raises(MigrationChecksumError):
        database.migrate()


@pytest.mark.migration
def test_unknown_sqlite_migration_blocks_startup(tmp_path):
    database_path = tmp_path / "foundation.sqlite3"
    database = SQLiteDatabase(database_path)
    database.migrate()

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO schema_migrations (migration_id, checksum, applied_at) "
            "VALUES (?, ?, ?)",
            ("9999_unknown", "0" * 64, "2026-10-17T00:00:00Z"),
        )

    with pytest.raises(UnknownMigrationError):
        database.migrate()
