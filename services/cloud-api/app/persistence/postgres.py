from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    cast,
    create_engine,
    inspect,
    insert,
    or_,
    select,
    text,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.types import UserDefinedType


metadata = MetaData()


class VectorType(UserDefinedType):
    """Dimension-agnostic pgvector column; the active profile owns dimensions."""

    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "vector"

    def bind_processor(self, dialect):
        def process(value):
            if value is None:
                return None
            return "[" + ",".join(format(float(item), ".17g") for item in value) + "]"

        return process

    def bind_expression(self, bindvalue):
        return cast(bindvalue, self)

schema_migrations = Table(
    "schema_migrations",
    metadata,
    Column("migration_id", String(128), primary_key=True),
    Column("checksum", String(64), nullable=False),
    Column("applied_at", DateTime(timezone=True), nullable=False),
)

users = Table(
    "users",
    metadata,
    Column("user_id", String(128), primary_key=True),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

organization_memberships = Table(
    "organization_memberships",
    metadata,
    Column("membership_id", String(128), primary_key=True),
    Column("organization_id", String(128), nullable=False),
    Column("user_id", String(128), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["user_id"], ["users.user_id"], name="fk_memberships_user"),
    UniqueConstraint(
        "organization_id", "membership_id", name="uq_memberships_org_membership"
    ),
    UniqueConstraint("organization_id", "user_id", name="uq_memberships_org_user"),
)

roles = Table(
    "roles",
    metadata,
    Column("role_id", String(128), primary_key=True),
    Column("organization_id", String(128), nullable=False),
    Column("name", String(128), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("organization_id", "role_id", name="uq_roles_org_role"),
    UniqueConstraint("organization_id", "name", name="uq_roles_org_name"),
)

permissions = Table(
    "permissions",
    metadata,
    Column("permission_id", String(128), primary_key=True),
    Column("permission_code", String(160), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("permission_code", name="uq_permissions_code"),
)

membership_roles = Table(
    "membership_roles",
    metadata,
    Column("organization_id", String(128), primary_key=True),
    Column("membership_id", String(128), primary_key=True),
    Column("role_id", String(128), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["organization_id", "membership_id"],
        ["organization_memberships.organization_id", "organization_memberships.membership_id"],
        name="fk_membership_roles_org_membership",
    ),
    ForeignKeyConstraint(
        ["organization_id", "role_id"],
        ["roles.organization_id", "roles.role_id"],
        name="fk_membership_roles_org_role",
    ),
)

role_permissions = Table(
    "role_permissions",
    metadata,
    Column("organization_id", String(128), primary_key=True),
    Column("role_id", String(128), primary_key=True),
    Column("permission_id", String(128), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["organization_id", "role_id"],
        ["roles.organization_id", "roles.role_id"],
        name="fk_role_permissions_org_role",
    ),
    ForeignKeyConstraint(
        ["permission_id"],
        ["permissions.permission_id"],
        name="fk_role_permissions_permission",
    ),
)

tasks = Table(
    "tasks",
    metadata,
    Column("task_id", String(128), primary_key=True),
    Column("organization_id", String(128), nullable=False),
    Column("status", String(32), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("organization_id", "task_id", name="uq_tasks_org_task"),
)

agent_runs = Table(
    "agent_runs",
    metadata,
    Column("run_id", String(128), primary_key=True),
    Column("organization_id", String(128), nullable=False),
    Column("task_id", String(128), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["organization_id", "task_id"],
        ["tasks.organization_id", "tasks.task_id"],
        name="fk_agent_runs_org_task",
    ),
    UniqueConstraint("organization_id", "run_id", name="uq_agent_runs_org_run"),
)

events = Table(
    "events",
    metadata,
    Column("event_id", String(128), primary_key=True),
    Column("organization_id", String(128), nullable=False),
    Column("idempotency_key", String(256), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "organization_id", "idempotency_key", name="uq_events_org_idempotency"
    ),
)

approvals = Table(
    "approvals",
    metadata,
    Column("approval_id", String(128), primary_key=True),
    Column("organization_id", String(128), nullable=False),
    Column("run_id", String(128)),
    Column("status", String(32), nullable=False),
    Column("action_digest", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["organization_id", "run_id"],
        ["agent_runs.organization_id", "agent_runs.run_id"],
        name="fk_approvals_org_run",
    ),
    UniqueConstraint("organization_id", "approval_id", name="uq_approvals_org_id"),
)

workers = Table(
    "workers",
    metadata,
    Column("worker_id", String(128), primary_key=True),
    Column("organization_id", String(128), nullable=False),
    Column("status", String(32), nullable=False),
    Column("worker_version", String(64), nullable=False, server_default="unknown"),
    Column("protocol_version", Integer, nullable=False, server_default="1"),
    Column("capabilities", JSON, nullable=False, server_default="[]"),
    Column("last_heartbeat_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("organization_id", "worker_id", name="uq_workers_org_worker"),
)

worker_leases = Table(
    "worker_leases",
    metadata,
    Column("lease_id", String(128), primary_key=True),
    Column("organization_id", String(128), nullable=False),
    Column("worker_id", String(128), nullable=False),
    Column("task_id", String(128), nullable=False),
    Column("fencing_token", BigInteger, nullable=False),
    Column("status", String(32), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("idempotency_key", String(256)),
    ForeignKeyConstraint(
        ["organization_id", "worker_id"],
        ["workers.organization_id", "workers.worker_id"],
        name="fk_worker_leases_org_worker",
    ),
    ForeignKeyConstraint(
        ["organization_id", "task_id"],
        ["tasks.organization_id", "tasks.task_id"],
        name="fk_worker_leases_org_task",
    ),
    UniqueConstraint(
        "organization_id", "worker_id", "fencing_token", name="uq_worker_fencing_token"
    ),
)
Index(
    "ix_worker_leases_org_task_status",
    worker_leases.c.organization_id,
    worker_leases.c.task_id,
    worker_leases.c.status,
)

knowledge_documents = Table(
    "knowledge_documents",
    metadata,
    Column("document_id", String(128), primary_key=True),
    Column("organization_id", String(128), nullable=False),
    Column("scope", String(128), nullable=False),
    Column("classification", String(64), nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "organization_id", "document_id", name="uq_knowledge_documents_org_document"
    ),
)

knowledge_chunks = Table(
    "knowledge_chunks",
    metadata,
    Column("chunk_id", String(256), primary_key=True),
    Column("organization_id", String(128), nullable=False),
    Column("document_id", String(128), nullable=False),
    Column("chunk_index", Integer, nullable=False),
    Column("content", Text, nullable=False),
    Column("source_reference", JSON, nullable=False),
    Column("embedding", VectorType(), nullable=False),
    ForeignKeyConstraint(
        ["organization_id", "document_id"],
        ["knowledge_documents.organization_id", "knowledge_documents.document_id"],
        name="fk_knowledge_chunks_org_document",
        ondelete="CASCADE",
    ),
    UniqueConstraint(
        "organization_id", "document_id", "chunk_index", name="uq_knowledge_chunks_order"
    ),
)

knowledge_document_acl = Table(
    "knowledge_document_acl",
    metadata,
    Column("organization_id", String(128), primary_key=True),
    Column("document_id", String(128), primary_key=True),
    Column("permission_code", String(160), primary_key=True),
    ForeignKeyConstraint(
        ["organization_id", "document_id"],
        ["knowledge_documents.organization_id", "knowledge_documents.document_id"],
        name="fk_knowledge_document_acl_org_document",
        ondelete="CASCADE",
    ),
)

schedules = Table(
    "schedules",
    metadata,
    Column("schedule_id", String(128), primary_key=True),
    Column("organization_id", String(128), nullable=False),
    Column("workflow_version_id", String(128), nullable=False),
    Column("trigger_json", JSON, nullable=False),
    Column("timezone", String(128), nullable=False),
    Column("coalesce", String(16), nullable=False),
    Column("misfire_grace_time", Integer, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("organization_id", "schedule_id", name="uq_schedules_org_schedule"),
)

schedule_occurrences = Table(
    "schedule_occurrences",
    metadata,
    Column("occurrence_id", String(128), primary_key=True),
    Column("organization_id", String(128), nullable=False),
    Column("schedule_id", String(128), nullable=False),
    Column("scheduled_at", DateTime(timezone=True), nullable=False),
    Column("idempotency_key", String(256), nullable=False),
    Column("status", String(32), nullable=False),
    Column("workflow_run_id", String(128)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["organization_id", "schedule_id"],
        ["schedules.organization_id", "schedules.schedule_id"],
        name="fk_schedule_occurrences_schedule",
    ),
    UniqueConstraint(
        "organization_id", "schedule_id", "scheduled_at", name="uq_schedule_occurrence_time"
    ),
    UniqueConstraint("idempotency_key", name="uq_schedule_occurrence_idempotency"),
    UniqueConstraint(
        "organization_id", "schedule_id", "occurrence_id", name="uq_schedule_occurrence_scope"
    ),
)

apscheduler_jobs = Table(
    "apscheduler_jobs",
    metadata,
    Column("id", String(191), primary_key=True),
    Column("next_run_time", Float),
    Column("job_state", LargeBinary, nullable=False),
)

Index("ix_events_org_received_at", events.c.organization_id, events.c.received_at)
Index("ix_worker_leases_org_status", worker_leases.c.organization_id, worker_leases.c.status)
Index(
    "ix_knowledge_documents_acl",
    knowledge_documents.c.organization_id,
    knowledge_documents.c.scope,
    knowledge_documents.c.classification,
)
Index(
    "ix_knowledge_chunks_org_document",
    knowledge_chunks.c.organization_id,
    knowledge_chunks.c.document_id,
)
Index(
    "ix_knowledge_document_acl_permission",
    knowledge_document_acl.c.organization_id,
    knowledge_document_acl.c.permission_code,
)
Index(
    "ix_memberships_org_user_status",
    organization_memberships.c.organization_id,
    organization_memberships.c.user_id,
    organization_memberships.c.status,
)
Index(
    "ix_membership_roles_org_role",
    membership_roles.c.organization_id,
    membership_roles.c.role_id,
)
Index(
    "ix_role_permissions_org_permission",
    role_permissions.c.organization_id,
    role_permissions.c.permission_id,
)
Index(
    "uq_schedule_occurrences_one_active",
    schedule_occurrences.c.schedule_id,
    unique=True,
    sqlite_where=schedule_occurrences.c.status.in_(["CLAIMED", "RUNNING"]),
    postgresql_where=schedule_occurrences.c.status.in_(["CLAIMED", "RUNNING"]),
)
Index(
    "ix_schedule_occurrences_pending",
    schedule_occurrences.c.schedule_id,
    schedule_occurrences.c.status,
    schedule_occurrences.c.scheduled_at,
)
Index("ix_apscheduler_jobs_next_run_time", apscheduler_jobs.c.next_run_time)


class MigrationStateError(RuntimeError):
    """The database schema state is unknown or cannot be verified safely."""


class MigrationChecksumError(MigrationStateError):
    """An applied migration differs from the source that was recorded."""


class UnknownMigrationError(MigrationStateError):
    """The database references a migration that this release does not know."""


class IdempotencyConflictError(RuntimeError):
    """An idempotency key is already bound to a different event."""


@dataclass(frozen=True)
class ServerAck:
    event_id: str
    idempotency_key: str
    accepted: bool = True
    duplicate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "idempotency_key": self.idempotency_key,
            "accepted": self.accepted,
            "duplicate": self.duplicate,
        }


def create_postgres_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("Cloud persistence requires a PostgreSQL URL")
    return create_engine(url, pool_pre_ping=True)


def _migration_scripts(config: Config) -> tuple[ScriptDirectory, dict[str, Path]]:
    directory = ScriptDirectory.from_config(config)
    scripts = {
        script.revision: Path(script.path)
        for script in directory.walk_revisions("base", "heads")
    }
    return directory, scripts


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _revision_chain(directory: ScriptDirectory, revision: str) -> list[str]:
    chain: list[str] = []
    current = directory.get_revision(revision)
    while current is not None:
        chain.append(current.revision)
        if current.down_revision is None:
            break
        if isinstance(current.down_revision, tuple):
            raise MigrationStateError(
                "merge revisions are not supported by the V0.1 migration verifier"
            )
        current = directory.get_revision(current.down_revision)
    return chain


def _current_revisions(connection) -> list[str]:
    if not inspect(connection).has_table("alembic_version"):
        return []
    return list(
        connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
    )


def verify_applied_migration_checksums(connection, config: Config) -> None:
    if not inspect(connection).has_table("schema_migrations"):
        return

    directory, scripts = _migration_scripts(config)
    stored = {
        row.migration_id: row.checksum
        for row in connection.execute(
            text("SELECT migration_id, checksum FROM schema_migrations")
        )
    }
    unknown_stored = set(stored) - set(scripts)
    if unknown_stored:
        raise UnknownMigrationError(
            "database contains unknown migration(s): "
            + ", ".join(sorted(unknown_stored))
        )

    current_revisions = _current_revisions(connection)
    unknown_heads = set(current_revisions) - set(scripts)
    if unknown_heads:
        raise UnknownMigrationError(
            "database references unknown Alembic revision(s): "
            + ", ".join(sorted(unknown_heads))
        )
    applied = {
        revision
        for head in current_revisions
        for revision in _revision_chain(directory, head)
    }
    if set(stored) != applied:
        raise MigrationStateError(
            "Alembic revision history and checksum ledger do not match"
        )

    for migration_id, checksum in stored.items():
        if checksum != _checksum(scripts[migration_id]):
            raise MigrationChecksumError(
                f"migration checksum mismatch: {migration_id}"
            )


def record_applied_migration_checksums(connection, config: Config) -> None:
    directory, scripts = _migration_scripts(config)
    applied = {
        revision
        for head in _current_revisions(connection)
        for revision in _revision_chain(directory, head)
    }
    for migration_id in sorted(applied):
        checksum = _checksum(scripts[migration_id])
        existing = connection.execute(
            text(
                "SELECT checksum FROM schema_migrations WHERE migration_id = :migration_id"
            ),
            {"migration_id": migration_id},
        ).scalar_one_or_none()
        if existing is not None:
            if existing != checksum:
                raise MigrationChecksumError(
                    f"migration checksum mismatch: {migration_id}"
                )
            continue
        connection.execute(
            text(
                "INSERT INTO schema_migrations (migration_id, checksum, applied_at) "
                "VALUES (:migration_id, :checksum, :applied_at)"
            ),
            {
                "migration_id": migration_id,
                "checksum": checksum,
                "applied_at": datetime.now(timezone.utc),
            },
        )


class PostgresPersistence:
    """Cloud Source of Truth adapter for Foundation event ingestion."""

    def __init__(self, engine: Engine):
        self._engine = engine

    def accept_event(
        self,
        *,
        organization_id: str,
        event_id: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
    ) -> ServerAck:
        if not all((organization_id, event_id, idempotency_key)):
            raise ValueError("organization_id, event_id and idempotency_key are required")
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        created_at = datetime.now(timezone.utc)

        def find_existing(connection):
            return connection.execute(
                select(events).where(
                    or_(
                        events.c.event_id == event_id,
                        and_(
                            events.c.organization_id == organization_id,
                            events.c.idempotency_key == idempotency_key,
                        ),
                    )
                )
            ).mappings().first()

        def acknowledge_existing(existing) -> ServerAck:
            if (
                existing["organization_id"] != organization_id
                or existing["idempotency_key"] != idempotency_key
                or existing["payload"] != dict(payload)
            ):
                raise IdempotencyConflictError(
                    "event_id or idempotency_key is already bound to another event"
                )
            return ServerAck(event_id, idempotency_key, duplicate=True)

        try:
            with self._engine.begin() as connection:
                existing = find_existing(connection)
                if existing is not None:
                    result = acknowledge_existing(existing)
                else:
                    connection.execute(
                        insert(events).values(
                            event_id=event_id,
                            organization_id=organization_id,
                            idempotency_key=idempotency_key,
                            payload=dict(payload),
                            received_at=created_at,
                        )
                    )
                    result = ServerAck(event_id, idempotency_key)
            return result
        except IntegrityError:
            # A concurrent submission can win the unique-key race after the first read.
            with self._engine.begin() as connection:
                existing = find_existing(connection)
                if existing is None:
                    raise
                return acknowledge_existing(existing)
