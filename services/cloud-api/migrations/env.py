from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

from app.persistence.postgres import (
    metadata,
    record_applied_migration_checksums,
    verify_applied_migration_checksums,
)


config = context.config
target_metadata = metadata


def _run_with_connection(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        verify_applied_migration_checksums(connection, config)
        context.run_migrations()
        record_applied_migration_checksums(connection, config)


def run_migrations_offline() -> None:
    database_url = os.environ.get("SHULIU_DATABASE_URL") or config.get_main_option(
        "sqlalchemy.url"
    )
    if not database_url:
        raise RuntimeError("SHULIU_DATABASE_URL is required to generate migration SQL")
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        _run_with_connection(supplied_connection)
        return

    database_url = os.environ.get("SHULIU_DATABASE_URL") or config.get_main_option(
        "sqlalchemy.url"
    )
    if not database_url:
        raise RuntimeError("SHULIU_DATABASE_URL is required to run migrations")
    connectable = create_engine(database_url, poolclass=pool.NullPool)
    try:
        with connectable.connect() as connection:
            _run_with_connection(connection)
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
