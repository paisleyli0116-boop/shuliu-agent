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
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    and_,
    create_engine,
    inspect,
    insert,
    or_,
    select,
    text,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError


metadata = MetaData()

schema_migrations = Table(
    "schema_migrations",
    metadata,
    Column("migration_id", String(128), primary_key=True),
    Column("checksum", String(64), nullable=False),
    Column("applied_at", DateTime(timezone=True), nullable=False),
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

Index("ix_events_org_received_at", events.c.organization_id, events.c.received_at)
Index("ix_worker_leases_org_status", worker_leases.c.organization_id, worker_leases.c.status)
Index(
    "ix_knowledge_documents_acl",
    knowledge_documents.c.organization_id,
    knowledge_documents.c.scope,
    knowledge_documents.c.classification,
)


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
