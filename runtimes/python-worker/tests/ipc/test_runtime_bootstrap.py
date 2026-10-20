from __future__ import annotations

import sqlite3

import pytest

from shuliu_runtime.ipc.bootstrap import RuntimeBootstrapError, bootstrap_runtime


def test_first_bootstrap_creates_database_under_the_user_data_directory(tmp_path):
    result = bootstrap_runtime(tmp_path)

    assert result.status == "healthy"
    assert result.database_path == tmp_path / "foundation.sqlite"
    assert result.database_schema == "1.0.0"

    with sqlite3.connect(result.database_path) as connection:
        migration_ids = connection.execute(
            "SELECT migration_id FROM schema_migrations ORDER BY migration_id"
        ).fetchall()

    assert migration_ids == [("0001_local_foundation",)]


def test_unknown_database_migration_fails_bootstrap_without_downgrading_or_erasing_data(tmp_path):
    database_path = tmp_path / "foundation.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations "
            "(migration_id TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?)",
            ("9999_future_schema", "preserve-this-checksum", "2026-10-20T00:00:00Z"),
        )

    with pytest.raises(RuntimeBootstrapError) as error:
        bootstrap_runtime(tmp_path)

    assert error.value.code == "DATABASE_SCHEMA_INCOMPATIBLE"
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT migration_id, checksum FROM schema_migrations"
        ).fetchone()
    assert row == ("9999_future_schema", "preserve-this-checksum")
