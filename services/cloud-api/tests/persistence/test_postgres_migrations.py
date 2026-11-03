from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

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
                "knowledge_chunks",
                "knowledge_document_acl",
                "knowledge_embedding_profiles",
                "schema_migrations",
                "users",
                "organization_memberships",
                "roles",
                "permissions",
                "membership_roles",
                "role_permissions",
                "schedules",
                "schedule_occurrences",
                "apscheduler_jobs",
                "worker_registration_tokens",
                "worker_session_tokens",
                "worker_auth_challenges",
                "tool_idempotency",
            } <= tables

            for table in {
                "tasks",
                "agent_runs",
                "events",
                "approvals",
                "workers",
                "worker_leases",
                "knowledge_documents",
                "knowledge_chunks",
                "knowledge_document_acl",
                "knowledge_embedding_profiles",
                "organization_memberships",
                "roles",
                "membership_roles",
                "role_permissions",
                "schedules",
                "schedule_occurrences",
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
            worker_columns = {
                column["name"] for column in inspector.get_columns("workers")
            }
            tool_idempotency_columns = {
                column["name"]
                for column in inspector.get_columns("tool_idempotency")
            }
            lease_columns = {
                column["name"] for column in inspector.get_columns("worker_leases")
            }
            assert "subject_id" not in tool_idempotency_columns
            tool_idempotency_unique = inspector.get_unique_constraints(
                "tool_idempotency"
            )
            assert any(
                item["column_names"]
                == ["organization_id", "tool_id", "operation_id", "idempotency_key"]
                for item in tool_idempotency_unique
            )
            assert {
                "worker_version",
                "protocol_version",
                "capabilities",
                "last_heartbeat_at",
            } <= worker_columns
            assert "idempotency_key" in lease_columns
            assert "ix_worker_leases_org_task_status" in {
                index["name"]
                for index in inspector.get_indexes("worker_leases")
            }
            fencing_constraints = inspector.get_unique_constraints("worker_leases")
            assert any(
                constraint["column_names"]
                == ["organization_id", "task_id", "fencing_token"]
                for constraint in fencing_constraints
            )
            assert {"device_key_algorithm", "device_public_key"} <= worker_columns
    finally:
        engine.dispose()


@pytest.mark.migration
def test_schedule_occurrences_enforce_one_active_run_per_schedule():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            upgrade_to_head(connection)
            connection.execute(
                text(
                    "INSERT INTO schedules "
                    "(schedule_id, organization_id, workflow_version_id, trigger_json, "
                    "timezone, coalesce, misfire_grace_time, enabled, created_at, updated_at) "
                    "VALUES ('schedule-1', 'org-1', 'workflow-v1', '{}', 'UTC', "
                    "'latest', 60, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            for index, occurrence_id in enumerate(("occurrence-1", "occurrence-2")):
                values = {
                    "id": occurrence_id,
                    "key": f"key:{occurrence_id}",
                    "scheduled_at": f"2026-10-18T00:0{index}:00+00:00",
                }
                if index == 1:
                    with pytest.raises(IntegrityError):
                        connection.execute(
                            text(
                                "INSERT INTO schedule_occurrences "
                                "(occurrence_id, organization_id, schedule_id, scheduled_at, "
                                "idempotency_key, status, created_at, updated_at) "
                                "VALUES (:id, 'org-1', 'schedule-1', :scheduled_at, "
                                ":key, 'RUNNING', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                            ),
                            values,
                        )
                else:
                    connection.execute(
                        text(
                            "INSERT INTO schedule_occurrences "
                            "(occurrence_id, organization_id, schedule_id, scheduled_at, "
                            "idempotency_key, status, created_at, updated_at) "
                            "VALUES (:id, 'org-1', 'schedule-1', :scheduled_at, "
                            ":key, 'RUNNING', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                        ),
                        values,
                    )
    finally:
        engine.dispose()


@pytest.mark.migration
def test_tool_idempotency_migration_enforces_scope_digest_and_state():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            upgrade_to_head(connection)
            inspector = inspect(connection)
            columns = {
                column["name"]
                for column in inspector.get_columns("tool_idempotency")
            }
            unique_constraints = inspector.get_unique_constraints("tool_idempotency")
            check_constraints = inspector.get_check_constraints("tool_idempotency")

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
        assert "subject_id" not in columns
        assert any(
            constraint["column_names"]
            == [
                "organization_id",
                "tool_id",
                "operation_id",
                "idempotency_key",
            ]
            for constraint in unique_constraints
        )
        assert any("INDETERMINATE" in constraint["sqltext"] for constraint in check_constraints)
        assert any("result_json" in constraint["sqltext"] for constraint in check_constraints)
        assert any("organization_id" in constraint["sqltext"] for constraint in check_constraints)
        assert any("idempotency_key" in constraint["sqltext"] for constraint in check_constraints)
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

@pytest.mark.migration
def test_task29_model_usage_migration_creates_org_scoped_safe_tables():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            upgrade_to_head(connection)
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            assert {"model_calls", "model_budget_accounts", "model_budget_reservations"} <= tables

            expected_columns = {
                "call_id", "organization_id", "trace_id", "requested_capability",
                "provider_id", "model", "model_version", "usage", "latency_ms",
                "cost", "error_code", "status", "created_at",
            }
            call_columns = {column["name"] for column in inspector.get_columns("model_calls")}
            assert expected_columns <= call_columns
            assert not {"prompt", "completion", "input", "output", "secret", "api_key"} & call_columns
            call_checks = inspector.get_check_constraints("model_calls")
            assert any("SUCCEEDED" in check["sqltext"] and "UNKNOWN" in check["sqltext"] for check in call_checks)

            for table in ("model_calls", "model_budget_accounts", "model_budget_reservations"):
                columns = {column["name"]: column for column in inspector.get_columns(table)}
                assert "organization_id" in columns
                assert columns["organization_id"]["nullable"] is False

            account_columns = {column["name"] for column in inspector.get_columns("model_budget_accounts")}
            assert {"budget_ref", "currency", "limit_amount", "reserved_amount", "spent_amount", "reservation_amount"} <= account_columns
            reservation_columns = {column["name"] for column in inspector.get_columns("model_budget_reservations")}
            assert {"call_id", "budget_ref", "provider_id", "trace_id", "reserved_amount", "settled_amount", "status", "usage"} <= reservation_columns
    finally:
        engine.dispose()


@pytest.mark.migration
def test_task29_model_usage_migration_downgrades_and_reapplies_cleanly():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.connect() as connection:
            config = Config(str(SERVICE_ROOT / "alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.commit()

            command.downgrade(config, "0007_task_scoped_fencing_tokens")
            connection.commit()
            tables_after_downgrade = set(inspect(connection).get_table_names())
            assert not {
                "model_calls",
                "model_budget_accounts",
                "model_budget_reservations",
            } & tables_after_downgrade
            assert "events" in tables_after_downgrade

            command.upgrade(config, "head")
            connection.commit()
            tables_after_reapply = set(inspect(connection).get_table_names())
            assert {
                "model_calls",
                "model_budget_accounts",
                "model_budget_reservations",
            } <= tables_after_reapply
    finally:
        engine.dispose()


@pytest.mark.migration
def test_task30_knowledge_embedding_profile_migration_is_additive_and_profile_scoped():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            upgrade_to_head(connection)
            inspector = inspect(connection)
            chunk_columns = {
                column["name"]: column
                for column in inspector.get_columns("knowledge_chunks")
            }
            assert {
                "embedding",
                "embedding_profile_id",
                "embedding_provider_id",
                "embedding_model",
                "embedding_dimensions",
                "embedding_index_version",
            } <= set(chunk_columns)
            assert all(
                chunk_columns[name]["nullable"]
                for name in (
                    "embedding_profile_id",
                    "embedding_provider_id",
                    "embedding_model",
                    "embedding_dimensions",
                    "embedding_index_version",
                )
            )
            assert "ix_knowledge_chunks_embedding_profile" in {
                index["name"] for index in inspector.get_indexes("knowledge_chunks")
            }
            checks = inspector.get_check_constraints("knowledge_chunks")
            assert any(
                check["name"] == "ck_knowledge_chunks_embedding_profile_complete"
                for check in checks
            )
            profile_table_columns = {
                column["name"]
                for column in inspector.get_columns("knowledge_embedding_profiles")
            }
            assert {
                "organization_id", "profile_id", "index_version", "provider_id",
                "model", "dimensions", "state", "updated_at",
            } <= profile_table_columns
            profile_checks = inspector.get_check_constraints("knowledge_embedding_profiles")
            assert any(
                check["name"] == "ck_knowledge_embedding_profiles_state"
                for check in profile_checks
            )

        with engine.connect() as connection:
            config = Config(str(SERVICE_ROOT / "alembic.ini"))
            config.attributes["connection"] = connection
            command.downgrade(config, "0008_model_usage_budget")
            connection.commit()
            chunk_columns = {
                column["name"] for column in inspect(connection).get_columns("knowledge_chunks")
            }
            assert "embedding" in chunk_columns
            assert "embedding_profile_id" not in chunk_columns
            assert "knowledge_embedding_profiles" not in inspect(connection).get_table_names()

            command.upgrade(config, "head")
            connection.commit()
            chunk_columns = {
                column["name"] for column in inspect(connection).get_columns("knowledge_chunks")
            }
            assert {"embedding", "embedding_profile_id", "embedding_dimensions"} <= chunk_columns
            assert "knowledge_embedding_profiles" in inspect(connection).get_table_names()
    finally:
        engine.dispose()


@pytest.mark.migration
@pytest.mark.parametrize(("dimensions", "index_version"), [(None, 1), (3, None)])
def test_task30_embedding_profile_check_rejects_partial_metadata(dimensions, index_version):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            upgrade_to_head(connection)
            connection.execute(
                text(
                    "INSERT INTO knowledge_documents "
                    "(document_id, organization_id, scope, classification, metadata, created_at) "
                    "VALUES ('doc-partial-profile', 'org-partial-profile', 'team:test', 'PUBLIC', '{}', CURRENT_TIMESTAMP)"
                )
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO knowledge_chunks "
                        "(chunk_id, organization_id, document_id, chunk_index, content, source_reference, embedding, "
                        "embedding_profile_id, embedding_provider_id, embedding_model, embedding_dimensions, embedding_index_version) "
                        "VALUES (:chunk_id, 'org-partial-profile', 'doc-partial-profile', 0, 'public', '{}', '[1,0]', "
                        "'profile', 'provider', 'model', :dimensions, :index_version)"
                    ),
                    {
                        "chunk_id": f"chunk-partial-profile-{dimensions}-{index_version}",
                        "dimensions": dimensions,
                        "index_version": index_version,
                    },
                )
    finally:
        engine.dispose()

@pytest.mark.migration
def test_worker_assignment_permission_is_seeded_without_default_role_grants():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    permission_id = "foundation.task.assign_worker"
    try:
        with engine.begin() as connection:
            upgrade_to_head(connection)
            permission = connection.execute(
                text(
                    "SELECT permission_id FROM permissions "
                    "WHERE permission_code = :permission_code"
                ),
                {"permission_code": permission_id},
            ).scalar_one()
            grant_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM role_permissions "
                    "WHERE permission_id = :permission_id"
                ),
                {"permission_id": permission_id},
            ).scalar_one()
        assert permission == permission_id
        assert grant_count == 0
    finally:
        engine.dispose()
