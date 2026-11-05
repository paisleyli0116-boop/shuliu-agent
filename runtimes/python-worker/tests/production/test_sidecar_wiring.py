from __future__ import annotations

import json
import sys
from threading import Event

import pytest

import shuliu_runtime.ipc.server as server_module
from shuliu_runtime.ipc.bootstrap import bootstrap_runtime
from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.production.composition import (
    CATALOG_ENVIRONMENT_KEY,
    POLICY_BUNDLE_ENVIRONMENT_KEY,
    attach_workflow_runtime,
    compose_production_dependencies,
)
from shuliu_runtime.tools.registry import ToolDefinition, ToolRegistry

ORGANIZATION_ID = "org-foundation"
ACTOR_ID = "user-owner"


class NoopExecutor:
    def execute(self, tool_id, args, context, idempotency_key):
        return {"ok": True}

    def verify(self, tool_id, args, output, context):
        return True


def noop_registry() -> ToolRegistry:
    registry = ToolRegistry()
    schema = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    registry.register(
        ToolDefinition(
            name="core.noop",
            version="1.0.0",
            input_schema=schema,
            output_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            permissions=("foundation.tool.invoke",),
            risk_level=RiskLevel.LOW,
            idempotency_required=False,
        ),
        NoopExecutor(),
    )
    return registry


def deployed_environment(tmp_path, *, allow_tool: bool) -> dict[str, str]:
    rules = [
        {
            "rule_id": "allow-noop",
            "action": "core.noop",
            "risk": "LOW",
            "decision": "ALLOW",
        }
    ]
    if not allow_tool:
        rules = []
    bundle = tmp_path / "policy.json"
    bundle.write_text(
        json.dumps({"policy_version": "foundation-v0.1", "rules": rules}),
        encoding="utf-8",
    )
    catalog = tmp_path / "workflows"
    catalog.mkdir(exist_ok=True)
    (catalog / "noop.json").write_text(
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
    return {
        "SHULIU_ACTOR_ID": ACTOR_ID,
        "SHULIU_ORGANIZATION_ID": ORGANIZATION_ID,
        "SHULIU_ACTOR_PERMISSIONS": "foundation.tool.invoke",
        "SHULIU_CONTEXT_PRIVACY": "internal",
        POLICY_BUNDLE_ENVIRONMENT_KEY: str(bundle),
        CATALOG_ENVIRONMENT_KEY: str(catalog),
    }


@pytest.fixture()
def clean_environment(monkeypatch):
    for key in (
        "SHULIU_ACTOR_ID",
        "SHULIU_ORGANIZATION_ID",
        "SHULIU_ACTOR_PERMISSIONS",
        "SHULIU_CONTEXT_PRIVACY",
        POLICY_BUNDLE_ENVIRONMENT_KEY,
        CATALOG_ENVIRONMENT_KEY,
    ):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_the_sidecar_reports_why_workflow_is_off(clean_environment, tmp_path) -> None:
    bootstrap = bootstrap_runtime(tmp_path / "data")

    attached = server_module._attach_production_workflow(bootstrap)

    assert attached.workflow_runtime is None
    assert attached.workflow_unavailable_code == "RUNTIME_IDENTITY_UNAVAILABLE"


def test_the_sidecar_attaches_workflow_when_configuration_is_complete(
    clean_environment, tmp_path
) -> None:
    for key, value in deployed_environment(tmp_path, allow_tool=True).items():
        clean_environment.setenv(key, value)
    bootstrap = bootstrap_runtime(tmp_path / "data")

    attached = server_module._attach_production_workflow(bootstrap)

    assert attached.workflow_runtime is not None
    assert attached.workflow_unavailable_code is None


def test_a_started_workflow_executes_its_tool_through_the_real_broker(tmp_path) -> None:
    bootstrap = bootstrap_runtime(tmp_path / "data")
    outcome = compose_production_dependencies(
        bootstrap.database,
        deployed_environment(tmp_path, allow_tool=True),
        tool_providers=[noop_registry()],
    )
    assert outcome.dependencies is not None
    attached = attach_workflow_runtime(
        bootstrap, outcome.dependencies, outcome.unavailable_code
    )
    handlers = attached.workflow_runtime.create_ipc_handlers()

    result = handlers["runtime.workflow.start"](
        {
            "workflow_id": "foundation.noop",
            "workflow_version_id": "1.0.0",
            "input": {},
        },
        Event(),
    )

    assert result["organization_id"] == ORGANIZATION_ID
    assert result["status"] == "SUCCEEDED"
    assert result["run_id"]


def test_a_policy_without_a_matching_rule_denies_the_tool(tmp_path) -> None:
    bootstrap = bootstrap_runtime(tmp_path / "data")
    outcome = compose_production_dependencies(
        bootstrap.database,
        deployed_environment(tmp_path, allow_tool=False),
        tool_providers=[noop_registry()],
    )
    assert outcome.dependencies is not None
    attached = attach_workflow_runtime(
        bootstrap, outcome.dependencies, outcome.unavailable_code
    )
    handlers = attached.workflow_runtime.create_ipc_handlers()

    result = handlers["runtime.workflow.start"](
        {
            "workflow_id": "foundation.noop",
            "workflow_version_id": "1.0.0",
            "input": {},
        },
        Event(),
    )

    assert result["status"] != "SUCCEEDED"


def test_an_unpublished_version_is_refused(tmp_path) -> None:
    bootstrap = bootstrap_runtime(tmp_path / "data")
    outcome = compose_production_dependencies(
        bootstrap.database,
        deployed_environment(tmp_path, allow_tool=True),
        tool_providers=[noop_registry()],
    )
    assert outcome.dependencies is not None
    attached = attach_workflow_runtime(
        bootstrap, outcome.dependencies, outcome.unavailable_code
    )
    handlers = attached.workflow_runtime.create_ipc_handlers()

    with pytest.raises(Exception) as error:
        handlers["runtime.workflow.start"](
            {
                "workflow_id": "foundation.noop",
                "workflow_version_id": "9.9.9",
                "input": {},
            },
            Event(),
        )

    assert getattr(error.value, "code", None) == "WORKFLOW_DEFINITION_UNAVAILABLE"


def test_the_sidecar_keeps_desktop_runtimes_off_without_configuration(
    clean_environment, tmp_path
) -> None:
    bootstrap = bootstrap_runtime(tmp_path / "data")

    attached = server_module._attach_production_workflow(bootstrap)

    assert attached.browser_runtime is None
    assert attached.computer_runtime is None
    assert attached.browser_unavailable_code == "RUNTIME_IDENTITY_UNAVAILABLE"
    assert attached.computer_unavailable_code == "RUNTIME_IDENTITY_UNAVAILABLE"


def test_the_sidecar_attaches_the_browser_when_origins_are_deployed(
    clean_environment, tmp_path
) -> None:
    environment = deployed_environment(tmp_path, allow_tool=True)
    environment[
        "SHULIU_BROWSER_ALLOWED_ORIGINS"
    ] = "http://127.0.0.1:8765,https://app.example.test"
    for key, value in environment.items():
        clean_environment.setenv(key, value)
    bootstrap = bootstrap_runtime(tmp_path / "data")

    attached = server_module._attach_production_workflow(bootstrap)

    assert attached.browser_runtime is not None
    assert attached.browser_unavailable_code is None
    assert attached.workflow_runtime is not None
    if sys.platform == "win32":
        assert attached.computer_runtime is not None
    else:
        assert attached.computer_runtime is None
        assert attached.computer_unavailable_code == "COMPUTER_RUNTIME_UNSUPPORTED"


def test_a_desktop_component_reports_only_the_capability_it_has() -> None:
    assert server_module._desktop_component(
        object(), None, "BROWSER_RUNTIME_NOT_CONFIGURED", extra={"execution_gate": "closed"}
    ) == {"status": "healthy", "execution_gate": "closed"}
    assert server_module._desktop_component(
        None,
        "BROWSER_ALLOWED_ORIGINS_UNAVAILABLE",
        "BROWSER_RUNTIME_NOT_CONFIGURED",
        extra={"execution_gate": "closed"},
    ) == {
        "status": "not_configured",
        "error_code": "BROWSER_ALLOWED_ORIGINS_UNAVAILABLE",
    }
