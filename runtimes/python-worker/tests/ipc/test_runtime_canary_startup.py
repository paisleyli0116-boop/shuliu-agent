import pytest

from shuliu_runtime.ipc import server


@pytest.mark.parametrize(("opt_in", "expected"), [(None, False), ("1", True), ("true", False)])
def test_runtime_health_reports_process_scoped_canary_gate_without_calling_provider(
    monkeypatch, opt_in, expected, tmp_path
):
    observed = {}

    class FakeIpcServer:
        def __init__(self, *, handlers, health_state, **_kwargs):
            observed["handlers"] = handlers
            observed["health_state"] = health_state

        def serve(self, output_stream):
            return None

    monkeypatch.setattr(server, "_runtime_data_directory", lambda: tmp_path)
    monkeypatch.setattr(server, "create_process_recovery_ipc_handlers", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(server, "create_provider_runtime_from_environment", lambda: object())
    monkeypatch.setattr(server, "create_provider_ipc_handlers", lambda _runtime: {"providers.test_connection": object()})
    monkeypatch.setattr(server, "IpcServer", FakeIpcServer)
    if opt_in is None:
        monkeypatch.delenv("SHULIU_MODEL_CONNECTION_TEST", raising=False)
    else:
        monkeypatch.setenv("SHULIU_MODEL_CONNECTION_TEST", opt_in)

    assert server.main() == 0
    assert observed["health_state"]["provider_connection_test_enabled"] is expected
    assert set(observed["handlers"]) == {"providers.test_connection"}
