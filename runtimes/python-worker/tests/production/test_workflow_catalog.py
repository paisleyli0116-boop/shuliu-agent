from __future__ import annotations

import json

import pytest

from shuliu_runtime.production.catalog import (
    FileWorkflowCatalog,
    WorkflowCatalogError,
    WorkflowCatalogUnavailable,
)


def definition(workflow_id: str = "foundation.noop", version: str = "1.0.0") -> dict[str, object]:
    return {
        "workflow_id": workflow_id,
        "workflow_version_id": version,
        "input_schema": {"type": "object", "additionalProperties": False},
        "steps": [
            {
                "id": "execute",
                "type": "tool",
                "tool_id": "core.noop",
                "arguments": {},
            }
        ],
    }


def write(directory, name: str, payload: object):
    path = directory / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_a_published_definition_is_resolved_by_id_and_version(tmp_path) -> None:
    write(tmp_path, "noop.json", definition())
    catalog = FileWorkflowCatalog(tmp_path).load()

    resolved = catalog("foundation.noop", "1.0.0")

    assert resolved is not None
    assert resolved.workflow_id == "foundation.noop"
    assert resolved.workflow_version_id == "1.0.0"


def test_a_version_that_was_not_published_is_not_substituted(tmp_path) -> None:
    write(tmp_path, "noop.json", definition())
    catalog = FileWorkflowCatalog(tmp_path).load()

    assert catalog("foundation.noop", "2.0.0") is None
    assert catalog("foundation.unknown", "1.0.0") is None


def test_an_absent_catalog_directory_is_unavailable(tmp_path) -> None:
    with pytest.raises(WorkflowCatalogUnavailable) as error:
        FileWorkflowCatalog(tmp_path / "missing").load()

    assert error.value.code == "WORKFLOW_CATALOG_UNAVAILABLE"


def test_a_catalog_without_definitions_is_an_error(tmp_path) -> None:
    # 空目录不是"没有工作流可用"，而是部署漏了定义文件，必须显式失败。
    with pytest.raises(WorkflowCatalogUnavailable) as error:
        FileWorkflowCatalog(tmp_path).load()

    assert error.value.code == "WORKFLOW_CATALOG_UNAVAILABLE"


def test_an_invalid_definition_fails_the_whole_load(tmp_path) -> None:
    write(tmp_path, "noop.json", definition())
    write(tmp_path, "broken.json", {"workflow_id": "foundation.broken"})

    with pytest.raises(WorkflowCatalogError) as error:
        FileWorkflowCatalog(tmp_path).load()

    assert error.value.code == "WORKFLOW_CATALOG_INVALID"


def test_a_malformed_file_fails_the_whole_load(tmp_path) -> None:
    write(tmp_path, "noop.json", definition())
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(WorkflowCatalogError) as error:
        FileWorkflowCatalog(tmp_path).load()

    assert error.value.code == "WORKFLOW_CATALOG_INVALID"


def test_two_files_publishing_the_same_version_are_rejected(tmp_path) -> None:
    write(tmp_path, "noop-a.json", definition())
    write(tmp_path, "noop-b.json", definition())

    with pytest.raises(WorkflowCatalogError) as error:
        FileWorkflowCatalog(tmp_path).load()

    assert error.value.code == "WORKFLOW_CATALOG_INVALID"


def test_several_versions_of_one_workflow_coexist(tmp_path) -> None:
    write(tmp_path, "noop-v1.json", definition(version="1.0.0"))
    write(tmp_path, "noop-v2.json", definition(version="1.1.0"))
    catalog = FileWorkflowCatalog(tmp_path).load()

    assert catalog("foundation.noop", "1.0.0").workflow_version_id == "1.0.0"
    assert catalog("foundation.noop", "1.1.0").workflow_version_id == "1.1.0"


def test_a_non_json_file_in_the_catalog_is_ignored(tmp_path) -> None:
    write(tmp_path, "noop.json", definition())
    (tmp_path / "README.txt").write_text("published workflows", encoding="utf-8")
    catalog = FileWorkflowCatalog(tmp_path).load()

    assert catalog("foundation.noop", "1.0.0") is not None


def test_the_catalog_cannot_be_used_before_it_is_loaded(tmp_path) -> None:
    write(tmp_path, "noop.json", definition())

    with pytest.raises(WorkflowCatalogError) as error:
        FileWorkflowCatalog(tmp_path)("foundation.noop", "1.0.0")

    assert error.value.code == "WORKFLOW_CATALOG_NOT_LOADED"
