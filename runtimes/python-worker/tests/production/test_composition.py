from __future__ import annotations

import json
from datetime import timedelta

import pytest

from shuliu_runtime.ipc.bootstrap import bootstrap_runtime
from shuliu_runtime.production.composition import (
    APPROVAL_TTL_ENVIRONMENT_KEY,
    CATALOG_ENVIRONMENT_KEY,
    POLICY_BUNDLE_ENVIRONMENT_KEY,
    attach_workflow_runtime,
    compose_production_dependencies,
)

ORGANIZATION_ID = "org-foundation"
ACTOR_ID = "user-owner"


def identity_environment() -> dict[str, str]:
    return {
        "SHULIU_ACTOR_ID": ACTOR_ID,
        "SHULIU_ORGANIZATION_ID": ORGANIZATION_ID,
        "SHULIU_ACTOR_PERMISSIONS": "foundation.approval.decide,foundation.task.create",
        "SHULIU_CONTEXT_PRIVACY": "internal",
    }


def policy_bundle(path) -> str:
    path.write_text(
        json.dumps(
            {
                "policy_version": "foundation-v0.1",
                "rules": [
                    {
                        "rule_id": "allow-task-create",
                        "action": "foundation.task.create",
                        "risk": "LOW",
                        "decision": "ALLOW",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def workflow_catalog(path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    (path / "noop.json").write_text(
        json.dumps(
            {
                "workflow_id": "foundation.noop",
                "workflow_version_id": "1.0.0",
                "input_schema": {"type": "object"},
                "steps": [
                    {
                        "id": "execute",
                        "type": "tool",
                        "tool_id": "core.noop",
                        "arguments": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return str(path)


@pytest.fixture()
def database(tmp_path):
    return bootstrap_runtime(tmp_path / "data").database


def test_absent_identity_keeps_workflow_disabled(database, tmp_path) -> None:
    outcome = compose_production_dependencies(
        database,
        {
            POLICY_BUNDLE_ENVIRONMENT_KEY: policy_bundle(tmp_path / "policy.json"),
            CATALOG_ENVIRONMENT_KEY: workflow_catalog(tmp_path / "workflows"),
        },
    )

    assert outcome.dependencies is None
    assert outcome.unavailable_code == "RUNTIME_IDENTITY_UNAVAILABLE"


def test_identity_without_a_policy_bundle_keeps_workflow_disabled(
    database, tmp_path
) -> None:
    outcome = compose_production_dependencies(
        database,
        {
            **identity_environment(),
            CATALOG_ENVIRONMENT_KEY: workflow_catalog(tmp_path / "workflows"),
        },
    )

    assert outcome.dependencies is None
    assert outcome.unavailable_code == "POLICY_BUNDLE_UNAVAILABLE"


def test_identity_without_a_catalog_keeps_workflow_disabled(database, tmp_path) -> None:
    outcome = compose_production_dependencies(
        database,
        {
            **identity_environment(),
            POLICY_BUNDLE_ENVIRONMENT_KEY: policy_bundle(tmp_path / "policy.json"),
        },
    )

    assert outcome.dependencies is None
    assert outcome.unavailable_code == "WORKFLOW_CATALOG_UNAVAILABLE"


def test_a_partial_identity_configuration_does_not_fall_back(
    database, tmp_path
) -> None:
    environment = identity_environment()
    environment.pop("SHULIU_ORGANIZATION_ID")

    outcome = compose_production_dependencies(
        database,
        {
            **environment,
            POLICY_BUNDLE_ENVIRONMENT_KEY: policy_bundle(tmp_path / "policy.json"),
            CATALOG_ENVIRONMENT_KEY: workflow_catalog(tmp_path / "workflows"),
        },
    )

    assert outcome.dependencies is None
    assert outcome.unavailable_code == "RUNTIME_IDENTITY_UNAVAILABLE"


def test_a_complete_configuration_builds_the_trusted_dependencies(
    database, tmp_path
) -> None:
    outcome = compose_production_dependencies(
        database,
        {
            **identity_environment(),
            POLICY_BUNDLE_ENVIRONMENT_KEY: policy_bundle(tmp_path / "policy.json"),
            CATALOG_ENVIRONMENT_KEY: workflow_catalog(tmp_path / "workflows"),
        },
    )

    assert outcome.unavailable_code is None
    assert outcome.dependencies is not None
    # 没有部署任何 Tool provider 时，注册表为空，而不是凭空造出工具。
    assert outcome.dependencies.registry.names() == ()
    assert outcome.dependencies.definition_resolver("foundation.noop", "1.0.0") is not None
    assert outcome.dependencies.run_context_provider()["organization_id"] == ORGANIZATION_ID


def test_the_approval_window_is_bounded(database, tmp_path) -> None:
    outcome = compose_production_dependencies(
        database,
        {
            **identity_environment(),
            POLICY_BUNDLE_ENVIRONMENT_KEY: policy_bundle(tmp_path / "policy.json"),
            CATALOG_ENVIRONMENT_KEY: workflow_catalog(tmp_path / "workflows"),
            APPROVAL_TTL_ENVIRONMENT_KEY: "120",
        },
    )

    assert outcome.dependencies is not None
    assert outcome.approval_ttl == timedelta(seconds=120)


@pytest.mark.parametrize("value", ["0", "-5", "forever", "90000"])
def test_an_invalid_approval_window_is_refused(database, tmp_path, value: str) -> None:
    outcome = compose_production_dependencies(
        database,
        {
            **identity_environment(),
            POLICY_BUNDLE_ENVIRONMENT_KEY: policy_bundle(tmp_path / "policy.json"),
            CATALOG_ENVIRONMENT_KEY: workflow_catalog(tmp_path / "workflows"),
            APPROVAL_TTL_ENVIRONMENT_KEY: value,
        },
    )

    assert outcome.dependencies is None
    assert outcome.unavailable_code == "APPROVAL_TTL_INVALID"


def test_a_blank_approval_window_uses_the_bounded_default(database, tmp_path) -> None:
    # 空白按"未配置"处理，与其它配置项语义一致；默认值本身是有界窗口。
    outcome = compose_production_dependencies(
        database,
        {
            **identity_environment(),
            POLICY_BUNDLE_ENVIRONMENT_KEY: policy_bundle(tmp_path / "policy.json"),
            CATALOG_ENVIRONMENT_KEY: workflow_catalog(tmp_path / "workflows"),
            APPROVAL_TTL_ENVIRONMENT_KEY: "  ",
        },
    )

    assert outcome.dependencies is not None
    assert outcome.approval_ttl == timedelta(seconds=900)


def test_attaching_the_runtime_keeps_the_bootstrap_usable(database, tmp_path) -> None:
    bootstrap = bootstrap_runtime(tmp_path / "data")
    outcome = compose_production_dependencies(
        bootstrap.database,
        {
            **identity_environment(),
            POLICY_BUNDLE_ENVIRONMENT_KEY: policy_bundle(tmp_path / "policy.json"),
            CATALOG_ENVIRONMENT_KEY: workflow_catalog(tmp_path / "workflows"),
        },
    )

    attached = attach_workflow_runtime(bootstrap, outcome.dependencies)

    assert attached.workflow_runtime is not None
    assert "runtime.workflow.start" in attached.workflow_runtime.create_ipc_handlers()
    assert attached.database_schema == bootstrap.database_schema


def test_attaching_without_dependencies_leaves_the_runtime_absent(tmp_path) -> None:
    bootstrap = bootstrap_runtime(tmp_path / "data")

    attached = attach_workflow_runtime(bootstrap, None)

    assert attached.workflow_runtime is None
