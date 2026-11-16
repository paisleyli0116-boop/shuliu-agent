from __future__ import annotations

import json
from pathlib import Path

from shuliu_runtime.workflow.definition import WorkflowDefinition, WorkflowDefinitionError


class WorkflowCatalogUnavailable(RuntimeError):
    """No published Workflow definitions are deployed, so Workflow must stay off."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class WorkflowCatalogError(ValueError):
    """The deployed Workflow catalogue cannot be trusted as written."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class FileWorkflowCatalog:
    """Serve reviewed Workflow definitions published as files.

    Definitions are read once, validated by the Workflow schema, and frozen. The
    resolver only ever answers exact (workflow_id, workflow_version_id) matches,
    so a request cannot be upgraded to a different published version.
    """

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)
        self._definitions: dict[tuple[str, str], WorkflowDefinition] | None = None

    def load(self) -> FileWorkflowCatalog:
        if not self._directory.is_dir():
            raise WorkflowCatalogUnavailable("WORKFLOW_CATALOG_UNAVAILABLE")
        paths = sorted(self._directory.glob("*.json"))
        if not paths:
            raise WorkflowCatalogUnavailable("WORKFLOW_CATALOG_UNAVAILABLE")

        definitions: dict[tuple[str, str], WorkflowDefinition] = {}
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
                raise WorkflowCatalogError("WORKFLOW_CATALOG_INVALID") from error
            try:
                definition = WorkflowDefinition.from_dict(payload)
            except WorkflowDefinitionError as error:
                raise WorkflowCatalogError("WORKFLOW_CATALOG_INVALID") from error
            key = (definition.workflow_id, definition.workflow_version_id)
            if key in definitions:
                raise WorkflowCatalogError("WORKFLOW_CATALOG_INVALID")
            definitions[key] = definition

        self._definitions = definitions
        return self

    def __call__(
        self, workflow_id: str, workflow_version_id: str
    ) -> WorkflowDefinition | None:
        if self._definitions is None:
            raise WorkflowCatalogError("WORKFLOW_CATALOG_NOT_LOADED")
        if not isinstance(workflow_id, str) or not isinstance(
            workflow_version_id, str
        ):
            return None
        return self._definitions.get((workflow_id, workflow_version_id))
