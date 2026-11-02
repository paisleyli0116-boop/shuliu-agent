from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shuliu_runtime.persistence.sqlite import (
    DATABASE_SCHEMA_VERSION,
    SQLiteDatabase,
)
from shuliu_runtime.workflow.sqlite_repository import SQLiteWorkflowRepository


class RuntimeBootstrapError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RuntimeBootstrapResult:
    status: str
    database_path: Path
    database_schema: str
    database: SQLiteDatabase
    workflow_repository: SQLiteWorkflowRepository


def bootstrap_runtime(data_directory: str | Path) -> RuntimeBootstrapResult:
    directory = Path(data_directory)
    database_path = directory / "foundation.sqlite"
    database = SQLiteDatabase(database_path)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        database.migrate()
    except Exception as error:
        raise RuntimeBootstrapError("DATABASE_SCHEMA_INCOMPATIBLE") from error
    return RuntimeBootstrapResult(
        status="healthy",
        database_path=database_path,
        database_schema=DATABASE_SCHEMA_VERSION,
        database=database,
        workflow_repository=SQLiteWorkflowRepository(database),
    )
