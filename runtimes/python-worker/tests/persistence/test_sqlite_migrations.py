from __future__ import annotations

import sqlite3

import pytest

from shuliu_runtime.persistence.sqlite import (
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
    assert migration_count == 1


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
