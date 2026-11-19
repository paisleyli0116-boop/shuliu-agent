"""Integration regressions for the desktop slice on the merged production root."""
from io import StringIO
from threading import Event
from uuid import uuid4

import pytest

from shuliu_runtime.ipc import server
from shuliu_runtime.ipc.bootstrap import bootstrap_runtime
from shuliu_runtime.production.composition import (
    attach_production_runtime, compose_production_runtime,
)
from .test_desktop_composition import FakeComputerBackend, environment


def test_sidecar_registers_manual_chat_without_invoking_provider(monkeypatch, tmp_path):
    observed = {}

    class Provider:
        def complete_chat(self, **_kwargs):
            raise AssertionError("startup must not invoke a Provider")

    class CapturingServer:
        def __init__(self, *, handlers, **_kwargs):
            observed.update(handlers)

        def serve(self, **_kwargs):
            pass

    monkeypatch.setattr(server, "create_provider_runtime_from_environment", lambda **_kwargs: Provider())
    monkeypatch.setattr(server, "create_provider_ipc_handlers", lambda runtime: {})
    monkeypatch.setattr(server, "IpcServer", CapturingServer)
    monkeypatch.setattr(server, "_isolate_protocol_output", StringIO)
    assert server._serve_bootstrapped_runtime(bootstrap_runtime(tmp_path)) == 0
    assert "chat.complete" in observed
    assert "runtime.recovery.begin" in observed


def attached_runtime(tmp_path, values=None):
    bootstrap = bootstrap_runtime(tmp_path / "data")
    outcome = compose_production_runtime(
        bootstrap.database, environment(tmp_path) if values is None else values,
        data_directory=tmp_path / "data", platform="nt",
        computer_backend=FakeComputerBackend(),
    )
    return attach_production_runtime(bootstrap, outcome)


def test_computer_ipc_uses_production_identity_policy_and_broker(tmp_path):
    attached = attached_runtime(tmp_path)
    handlers = getattr(attached, "computer_ipc_handlers", {})
    assert "computer.capability" in handlers
    result = handlers["computer.capability"]({"trace_id": str(uuid4())}, Event())
    assert result["status"] == "succeeded"
    assert result["verification_status"] == "VERIFIED"
    assert result["policy_version"] == "foundation-v0.1"
    assert not any(name in handlers for name in ("computer.act", "computer.click", "computer.type"))


def test_computer_ipc_cannot_construct_fallback_identity(tmp_path):
    attached = attached_runtime(tmp_path, {})
    assert not getattr(attached, "computer_ipc_handlers", {})
    assert attached.computer_unavailable_code == "RUNTIME_IDENTITY_UNAVAILABLE"


def test_computer_ipc_rejects_forged_identity_and_permission(tmp_path):
    attached = attached_runtime(tmp_path)
    handler = getattr(attached, "computer_ipc_handlers", {}).get("computer.capability")
    assert callable(handler)
    with pytest.raises(ValueError):
        handler({"trace_id": str(uuid4()), "actor": {"subject_id": "admin"}}, Event())


def test_computer_ipc_cannot_bypass_production_policy(tmp_path):
    import json
    values = environment(tmp_path)
    (tmp_path / "policy.json").write_text(
        json.dumps({"policy_version": "deny-all", "rules": []}), encoding="utf-8",
    )
    attached = attached_runtime(tmp_path, values)
    handler = getattr(attached, "computer_ipc_handlers", {}).get("computer.capability")
    assert callable(handler)
    result = handler({"trace_id": str(uuid4())}, Event())
    assert result["status"] == "blocked"
    assert result["safe_error_code"] == "POLICY_DENIED"
