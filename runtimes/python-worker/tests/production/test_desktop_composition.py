from __future__ import annotations

import json
from pathlib import Path

import pytest

from shuliu_runtime.ipc.bootstrap import bootstrap_runtime
from shuliu_runtime.persistence.sqlite_idempotency import SQLiteIdempotencyStore
from shuliu_runtime.production.composition import (
    BROWSER_ORIGINS_ENVIRONMENT_KEY,
    CATALOG_ENVIRONMENT_KEY,
    POLICY_BUNDLE_ENVIRONMENT_KEY,
    compose_production_runtime,
)
from shuliu_runtime.production.tools import ToolRegistryCompositionError
from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.tools.broker import ToolExecutionError
from shuliu_runtime.tools.idempotency import InMemoryIdempotencyStore
from shuliu_runtime.tools.registry import ToolDefinition, ToolRegistry
from tests.fakes.fake_browser import FakeBrowser

ORGANIZATION_ID = "org-foundation"
ACTOR_ID = "user-owner"
LOOPBACK_ORIGIN = "http://127.0.0.1:8765"


class FakeComputerBackend:
    """Minimal backend double; proves wiring only, never Computer Use evidence."""

    def capability(self):
        return {"available": True, "session_id": 1}

    def observe(self, window_ref):
        return {"window_ref": window_ref, "elements": [], "dpi": 96}

    def perform(self, window_ref, action, element_id):
        return None


def identity_environment() -> dict[str, str]:
    return {
        "SHULIU_ACTOR_ID": ACTOR_ID,
        "SHULIU_ORGANIZATION_ID": ORGANIZATION_ID,
        "SHULIU_ACTOR_PERMISSIONS": (
            "browser.read,browser.act,computer.read,computer.act,"
            "foundation.approval.decide"
        ),
        "SHULIU_CONTEXT_PRIVACY": "internal",
    }


def policy_bundle(path: Path) -> str:
    rules = []
    for action, risk in (
        ("browser.launch", "LOW"),
        ("browser.observe", "LOW"),
        ("browser.close", "LOW"),
        ("browser.navigate", "CRITICAL"),
        ("browser.click", "CRITICAL"),
        ("browser.type", "CRITICAL"),
        ("browser.scroll", "CRITICAL"),
        ("computer.capability", "LOW"),
        ("computer.observe", "LOW"),
        ("computer.act", "CRITICAL"),
    ):
        rules.append(
            {
                "rule_id": "allow-" + action.replace(".", "-"),
                "action": action,
                "risk": risk,
                # 生产上写操作一律 REQUIRE_APPROVAL，不配 ALLOW：CRITICAL 动作
                # 必须拿到人审过的 approval context 才能进入执行器。
                "decision": "REQUIRE_APPROVAL" if risk == "CRITICAL" else "ALLOW",
            }
        )
    path.write_text(
        json.dumps({"policy_version": "foundation-v0.1", "rules": rules}),
        encoding="utf-8",
    )
    return str(path)


def workflow_catalog(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    (path / "noop.json").write_text(
        json.dumps(
            {
                "workflow_id": "foundation.noop",
                "workflow_version_id": "1.0.0",
                "input_schema": {"type": "object"},
                "steps": [
                    {"id": "execute", "type": "tool", "tool_id": "core.noop", "arguments": {}}
                ],
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def environment(tmp_path: Path, *, origins: str | None = LOOPBACK_ORIGIN) -> dict[str, str]:
    result = {
        **identity_environment(),
        POLICY_BUNDLE_ENVIRONMENT_KEY: policy_bundle(tmp_path / "policy.json"),
        CATALOG_ENVIRONMENT_KEY: workflow_catalog(tmp_path / "workflows"),
    }
    if origins is not None:
        result[BROWSER_ORIGINS_ENVIRONMENT_KEY] = origins
    return result


@pytest.fixture()
def database(tmp_path):
    return bootstrap_runtime(tmp_path / "data").database


def compose(database, tmp_path, *, environment_overrides=None, **kwargs):
    values = environment(tmp_path)
    if environment_overrides is not None:
        values = environment_overrides(values)
    return compose_production_runtime(
        database,
        values,
        data_directory=tmp_path / "data",
        browser_backend_factory=kwargs.pop("browser_backend_factory", FakeBrowser),
        computer_backend=kwargs.pop("computer_backend", FakeComputerBackend()),
        platform=kwargs.pop("platform", "nt"),
        **kwargs,
    )


def actor() -> dict[str, object]:
    return {"subject_id": ACTOR_ID, "organization_id": ORGANIZATION_ID}


class _UnusedExecutor:
    def execute(self, tool_id, args, context, idempotency_key):
        return {}

    def verify(self, tool_id, args, output, context):
        return True


def test_absent_identity_disables_every_runtime(database, tmp_path) -> None:
    values = environment(tmp_path)
    values.pop("SHULIU_ORGANIZATION_ID")

    outcome = compose_production_runtime(
        database, values, data_directory=tmp_path / "data"
    )

    assert outcome.workflow.dependencies is None
    assert outcome.workflow.unavailable_code == "RUNTIME_IDENTITY_UNAVAILABLE"
    assert outcome.desktop.browser is None
    assert outcome.desktop.computer is None
    assert outcome.desktop.browser_unavailable_code == "RUNTIME_IDENTITY_UNAVAILABLE"
    assert outcome.desktop.tool_providers == ()


def test_missing_browser_origins_disables_only_the_browser(database, tmp_path) -> None:
    outcome = compose(
        database,
        tmp_path,
        environment_overrides=lambda values: {
            key: value
            for key, value in values.items()
            if key != BROWSER_ORIGINS_ENVIRONMENT_KEY
        },
    )

    assert outcome.workflow.dependencies is not None
    assert outcome.desktop.browser is None
    assert outcome.desktop.browser_unavailable_code == "BROWSER_ALLOWED_ORIGINS_UNAVAILABLE"
    assert outcome.desktop.computer is not None
    assert outcome.desktop.computer_unavailable_code is None


def test_an_invalid_origin_keeps_the_browser_closed(database, tmp_path) -> None:
    outcome = compose(
        database,
        tmp_path,
        environment_overrides=lambda values: {
            **values,
            BROWSER_ORIGINS_ENVIRONMENT_KEY: "file:///C:/Windows",
        },
    )

    assert outcome.desktop.browser is None
    assert outcome.desktop.browser_unavailable_code == "BROWSER_ALLOWED_ORIGINS_INVALID"


def test_a_complete_configuration_wires_both_desktop_runtimes(database, tmp_path) -> None:
    outcome = compose(database, tmp_path)

    assert outcome.desktop.browser is not None
    assert outcome.desktop.computer is not None
    names = {
        name
        for provider in outcome.desktop.tool_providers
        for name in provider.names()
    }
    assert names == {
        "browser.launch",
        "browser.navigate",
        "browser.observe",
        "browser.click",
        "browser.type",
        "browser.scroll",
        "browser.close",
        "computer.capability",
        "computer.observe",
        "computer.act",
    }


def test_the_workflow_broker_reaches_the_browser_tools(database, tmp_path) -> None:
    outcome = compose(database, tmp_path)

    assert outcome.workflow.dependencies is not None
    assert "browser.launch" in outcome.workflow.dependencies.registry.names()
    assert "computer.act" in outcome.workflow.dependencies.registry.names()


def test_a_browser_write_without_approval_never_dispatches(database, tmp_path) -> None:
    backend = FakeBrowser()
    outcome = compose(database, tmp_path, browser_backend_factory=lambda: backend)
    browser = outcome.desktop.browser
    assert browser is not None
    context = {
        "organization_id": ORGANIZATION_ID,
        "trace_id": "trace-desktop-1",
        "privacy": "internal",
        "action_summary": "Production write requires human approval",
        "target_approver_id": ACTOR_ID,
    }
    try:
        session = browser.call(
            "launch", {}, actor=actor(), context=context
        ).output["session_id"]
        with pytest.raises(ToolExecutionError) as error:
            browser.call(
                "navigate",
                {"session_id": session, "url": LOOPBACK_ORIGIN + "/"},
                actor=actor(),
                context=context,
                idempotency_key="production-gate",
            )
    finally:
        browser.close()

    assert error.value.code == "APPROVAL_REQUIRED"
    assert "navigate" not in backend.calls


def test_the_browser_execution_gate_cannot_be_opened_by_configuration(
    database, tmp_path
) -> None:
    outcome = compose(database, tmp_path)
    browser = outcome.desktop.browser
    assert browser is not None

    # Composition root invariant: no environment value, and therefore no
    # Renderer payload, can supply an execution gate.
    assert browser._executor.gate is None


def test_the_browser_uses_the_durable_idempotency_store(database, tmp_path) -> None:
    outcome = compose(database, tmp_path)
    browser = outcome.desktop.browser
    assert browser is not None

    assert isinstance(browser.broker._idempotency_store, SQLiteIdempotencyStore)
    assert not isinstance(browser.broker._idempotency_store, InMemoryIdempotencyStore)


def test_the_computer_uses_the_durable_idempotency_store(database, tmp_path) -> None:
    outcome = compose(database, tmp_path)
    computer = outcome.desktop.computer
    assert computer is not None

    assert isinstance(computer.broker._idempotency_store, SQLiteIdempotencyStore)


def test_the_computer_is_unavailable_off_windows(database, tmp_path) -> None:
    outcome = compose(database, tmp_path, platform="posix")

    assert outcome.desktop.computer is None
    assert outcome.desktop.computer_unavailable_code == "COMPUTER_RUNTIME_UNSUPPORTED"
    assert outcome.desktop.browser is not None


def test_evidence_is_written_below_the_runtime_data_directory(database, tmp_path) -> None:
    outcome = compose(database, tmp_path)
    browser = outcome.desktop.browser
    assert browser is not None
    context = {
        "organization_id": ORGANIZATION_ID,
        "trace_id": "trace-desktop-2",
        "privacy": "internal",
    }
    try:
        browser.call("launch", {}, actor=actor(), context=context)
    finally:
        browser.close()

    written = list((tmp_path / "data" / "evidence").glob("*.json"))
    assert written, "browser evidence must be persisted, not only referenced"
    records = [json.loads(path.read_text(encoding="utf-8")) for path in written]
    assert any(record.get("trace_id") == "trace-desktop-2" for record in records)


def test_a_conflicting_tool_provider_is_refused(database, tmp_path) -> None:
    registry = ToolRegistry()
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    registry.register(
        ToolDefinition(
            "browser.launch",
            "1.0.0",
            schema,
            schema,
            ("browser.read",),
            RiskLevel.LOW,
            False,
        ),
        _UnusedExecutor(),
    )

    with pytest.raises(ToolRegistryCompositionError) as error:
        compose(database, tmp_path, tool_providers=[registry])

    assert error.value.code == "TOOL_REGISTRY_CONFLICT"


def test_a_browser_failure_does_not_take_the_workflow_down(database, tmp_path) -> None:
    profiles = tmp_path / "data" / "browser-profiles"
    profiles.parent.mkdir(parents=True, exist_ok=True)
    profiles.write_text("blocked", encoding="utf-8")

    outcome = compose(database, tmp_path)

    assert outcome.workflow.dependencies is not None
    assert outcome.desktop.browser is None
    assert outcome.desktop.browser_unavailable_code == "BROWSER_RUNTIME_UNAVAILABLE"
    assert "browser.launch" not in outcome.workflow.dependencies.registry.names()
    assert "computer.act" in outcome.workflow.dependencies.registry.names()
