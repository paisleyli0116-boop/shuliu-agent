from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
from typing import Any
from pathlib import Path

from shuliu_runtime.persistence.sqlite import (
    DATABASE_SCHEMA_VERSION,
    SQLiteDatabase,
)
from shuliu_runtime.workflow.sqlite_repository import SQLiteWorkflowRepository
from shuliu_runtime.workflow.composition import (
    ProductionWorkflowDependencies,
    ProductionWorkflowRuntime,
)


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
    workflow_runtime: ProductionWorkflowRuntime | None = None
    # Set when the trusted Workflow composition could not be built, so health can
    # report why Workflow is off instead of a bare `not_configured`.
    workflow_unavailable_code: str | None = None
    # Desktop runtimes are attached only when their trusted dependencies exist.
    # A `None` runtime with a code means "off", never "unrestricted".
    browser_runtime: object | None = None
    computer_runtime: object | None = None
    browser_unavailable_code: str | None = None
    computer_unavailable_code: str | None = None
    computer_ipc_handlers: Mapping[str, Any] = field(default_factory=dict)
    chat_principal: Any | None = None
    chat_policy: Any | None = None


def bootstrap_runtime(
    data_directory: str | Path,
    *,
    workflow_dependencies: ProductionWorkflowDependencies | None = None,
) -> RuntimeBootstrapResult:
    directory = Path(data_directory)
    database_path = directory / "foundation.sqlite"
    database = SQLiteDatabase(database_path)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        database.migrate()
    except Exception as error:
        raise RuntimeBootstrapError("DATABASE_SCHEMA_INCOMPATIBLE") from error
    workflow_repository = SQLiteWorkflowRepository(database)
    workflow_runtime = (
        ProductionWorkflowRuntime.build(
            database=database,
            repository=workflow_repository,
            dependencies=workflow_dependencies,
        )
        if workflow_dependencies is not None
        else None
    )
    return RuntimeBootstrapResult(
        status="healthy",
        database_path=database_path,
        database_schema=DATABASE_SCHEMA_VERSION,
        database=database,
        workflow_repository=workflow_repository,
        workflow_runtime=workflow_runtime,
    )
