from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.persistence.postgres import (
    IdempotencyConflictError,
    MigrationChecksumError,
    PostgresPersistence,
    UnknownMigrationError,
    create_postgres_engine,
)


SERVICE_ROOT = Path(__file__).resolve().parents[2]


def upgrade_to_head(connection):
    config = Config(str(SERVICE_ROOT / "alembic.ini"))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


@pytest.mark.migration
def test_foundation_postgres_migration_creates_org_scoped_tables():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            upgrade_to_head(connection)
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())

            assert {
                "tasks",
                "agent_runs",
                "events",
                "approvals",
                "workers",
                "worker_leases",
                "knowledge_documents",
                "schema_migrations",
            } <= tables

            for table in {
                "tasks",
                "agent_runs",
                "events",
                "approvals",
                "workers",
                "worker_leases",
                "knowledge_documents",
            }:
                organization_id = next(
                    column
                    for column in inspector.get_columns(table)
                    if column["name"] == "organization_id"
                )
                assert organization_id["nullable"] is False

            event_primary_key = inspector.get_pk_constraint("events")
            assert event_primary_key["constrained_columns"] == ["event_id"]
            event_constraints = inspector.get_unique_constraints("events")
            assert any(
                "idempotency_key" in item["column_names"]
                for item in event_constraints
            )
    finally:
        engine.dispose()


@pytest.mark.migration
def test_modified_applied_postgres_migration_checksum_blocks_startup():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            upgrade_to_head(connection)
            connection.execute(
                text("UPDATE schema_migrations SET checksum = :checksum"),
                {"checksum": "0" * 64},
            )
            with pytest.raises(MigrationChecksumError):
                upgrade_to_head(connection)
    finally:
        engine.dispose()


@pytest.mark.migration
def test_unknown_postgres_revision_blocks_migration():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            upgrade_to_head(connection)
            connection.execute(
                text("UPDATE alembic_version SET version_num = '9999_unknown'")
            )
            with pytest.raises(UnknownMigrationError):
                upgrade_to_head(connection)
    finally:
        engine.dispose()


@pytest.mark.migration
def test_server_event_submission_is_deduplicated_and_acknowledged_after_commit():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.connect() as connection:
            upgrade_to_head(connection)
            connection.commit()

        persistence = PostgresPersistence(engine)
        first = persistence.accept_event(
            organization_id="org-1",
            event_id="evt-1",
            idempotency_key="idem-1",
            payload={"kind": "foundation.event"},
        )
        duplicate = persistence.accept_event(
            organization_id="org-1",
            event_id="evt-1",
            idempotency_key="idem-1",
            payload={"kind": "foundation.event"},
        )

        assert first.accepted is True
        assert duplicate.accepted is True
        assert duplicate.duplicate is True
        assert first.event_id == duplicate.event_id == "evt-1"
        with engine.connect() as connection:
            count = connection.execute(
                text("SELECT COUNT(*) FROM events WHERE event_id = 'evt-1'")
            ).scalar_one()
        assert count == 1
    finally:
        engine.dispose()


@pytest.mark.migration
def test_server_rejects_idempotency_key_reuse_with_different_payload():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.connect() as connection:
            upgrade_to_head(connection)
            connection.commit()

        persistence = PostgresPersistence(engine)
        persistence.accept_event(
            organization_id="org-1",
            event_id="evt-1",
            idempotency_key="idem-1",
            payload={"kind": "first"},
        )
        with pytest.raises(IdempotencyConflictError):
            persistence.accept_event(
                organization_id="org-1",
                event_id="evt-2",
                idempotency_key="idem-1",
                payload={"kind": "different"},
            )
    finally:
        engine.dispose()


@pytest.mark.migration
def test_configured_postgres_migration_enables_pgvector_and_accepts_events():
    database_url = os.environ.get("SHULIU_DATABASE_URL")
    if not database_url:
        pytest.skip("SHULIU_DATABASE_URL is not configured")

    engine = create_postgres_engine(database_url)
    try:
        with engine.connect() as connection:
            upgrade_to_head(connection)
            connection.commit()
            extension = connection.execute(
                text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
            ).scalar_one_or_none()
        assert extension == "vector"

        persistence = PostgresPersistence(engine)
        first = persistence.accept_event(
            organization_id="org-1",
            event_id="evt-pg-1",
            idempotency_key="idem-pg-1",
            payload={"kind": "foundation.event"},
        )
        duplicate = persistence.accept_event(
            organization_id="org-1",
            event_id="evt-pg-1",
            idempotency_key="idem-pg-1",
            payload={"kind": "foundation.event"},
        )
        assert first.duplicate is False
        assert duplicate.duplicate is True
    finally:
        engine.dispose()
