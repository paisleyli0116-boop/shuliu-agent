from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from shuliu_runtime.ipc.server import IpcProtocolError, IpcServer, validate_runtime_ipc_message


def _message(message_type: str, payload: dict[str, object], request_id: str = "request-1") -> dict[str, object]:
    return {
        "protocol_name": "runtime-ipc",
        "protocol_version": 1,
        "message_id": f"message-{request_id}",
        "request_id": request_id,
        "trace_id": "trace-test",
        "message_type": message_type,
        "payload": payload,
    }


def test_runtime_ipc_schema_accepts_request_and_cancel_but_rejects_malformed_payloads() -> None:
    validate_runtime_ipc_message(_message("request", {"method": "runtime.health", "params": {}}))
    validate_runtime_ipc_message(_message("cancel", {}))

    for value in (
        _message("request", {"method": "runtime.health"}),
        _message("response", {"error": {"code": "secret-leak"}}),
        _message("execute", {"command": "rm -rf /"}),
    ):
        try:
            validate_runtime_ipc_message(value)
        except IpcProtocolError:
            continue
        raise AssertionError(f"invalid IPC frame was accepted: {value.get('message_type')}")


def test_safe_mode_release_commits_before_a_late_ipc_cancellation():
    import io

    def complete(_params, cancelled):
        cancelled.set()
        return {"status": "RESUME", "error_code": None}

    server = IpcServer(handlers={"runtime.recovery.complete": complete}, safe_mode=True)
    request = _message(
        "request",
        {
            "method": "runtime.recovery.complete",
            "params": {"transition_id": "11111111-1111-4111-8111-111111111111", "current_generation": 1},
        },
    )
    output = io.StringIO()

    server.serve(io.StringIO(json.dumps(request) + "\n"), output, io.StringIO())

    response = json.loads(output.getvalue())
    assert response["payload"]["result"] == {"status": "RESUME", "error_code": None}
    assert server._safe_mode is False
    assert server._health_state["status"] == "healthy"


def test_runtime_server_blocks_normal_ipc_until_startup_recovery_commits():
    import io

    transition_id = "11111111-1111-4111-8111-111111111111"
    invoked: list[bool] = []
    server = IpcServer(
        handlers={
            "runtime.recovery.begin": lambda params, _cancelled: {
                "status": "RESUME",
                "error_code": None,
                "previous_generation": 0,
                "current_generation": 1,
                "transition_id": params["transition_id"],
            },
            "runtime.recovery.complete": lambda _params, _cancelled: {
                "status": "RESUME",
                "error_code": None,
            },
            "test.normal": lambda _params, _cancelled: invoked.append(True) or {"ok": True},
        },
        recovery_required=True,
    )

    def exchange(message: dict[str, object]) -> dict[str, object]:
        output = io.StringIO()
        server.serve(io.StringIO(json.dumps(message) + "\n"), output, io.StringIO())
        return json.loads(output.getvalue())

    normal = exchange(_message("request", {"method": "test.normal", "params": {}}))
    assert normal["payload"]["error"]["code"] == "RUNTIME_SAFE_MODE"
    assert invoked == []

    began = exchange(_message(
        "request",
        {"method": "runtime.recovery.begin", "params": {"transition_id": transition_id}},
    ))
    assert began["payload"]["result"]["status"] == "RESUME"
    complete = exchange(_message(
        "request",
        {
            "method": "runtime.recovery.complete",
            "params": {"transition_id": transition_id, "current_generation": 1},
        },
    ))
    assert complete["payload"]["result"] == {"status": "RESUME", "error_code": None}

    resumed = exchange(_message("request", {"method": "test.normal", "params": {}}))
    assert resumed["payload"]["result"] == {"ok": True}
    assert invoked == [True]


def test_python_sidecar_reads_ndjson_and_writes_only_protocol_frames_to_stdout(tmp_path) -> None:
    repository = Path(__file__).resolve().parents[4]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repository / "runtimes" / "python-worker")
    env["SHULIU_DATA_DIR"] = str(tmp_path)
    env.pop("SHULIU_MODEL_PROFILES_JSON", None)
    env.pop("SHULIU_MODEL_CONNECTION_TEST", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "shuliu_runtime.ipc.server"],
        cwd=repository,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None

    def exchange(request_id: str, method: str, params: dict[str, object]) -> dict[str, object]:
        request = _message("request", {"method": method, "params": params}, request_id)
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        return json.loads(process.stdout.readline())

    handshake = exchange("request-1", "runtime.handshake", {})
    health_before_recovery = exchange("request-2", "runtime.health", {})
    denied_before_recovery = exchange("request-3", "tool.execute", {})
    transition_id = "11111111-1111-4111-8111-111111111111"
    recovery_begin = exchange("request-4", "runtime.recovery.begin", {"transition_id": transition_id})
    checkpoints = exchange("request-5", "runtime.recovery.checkpoints", {})
    recovery_complete = exchange(
        "request-6",
        "runtime.recovery.complete",
        {"transition_id": transition_id, "current_generation": 1},
    )
    health_after_recovery = exchange("request-7", "runtime.health", {})
    denied_unknown = exchange("request-8", "tool.execute", {})
    process.stdin.write('{"authorization":"python-test-secret"\n')
    process.stdin.flush()
    process.stdin.close()
    process.wait(timeout=5)
    process.stdin.close()

    responses = [
        handshake,
        health_before_recovery,
        denied_before_recovery,
        recovery_begin,
        checkpoints,
        recovery_complete,
        health_after_recovery,
        denied_unknown,
    ]
    assert [item["request_id"] for item in responses] == [f"request-{index}" for index in range(1, 9)]
    assert all(item["message_type"] == "response" for item in responses)
    assert all(item["protocol_name"] == "runtime-ipc" and item["protocol_version"] == 1 for item in responses)
    assert health_before_recovery["payload"]["result"]["status"] == "safe_mode"
    assert denied_before_recovery["payload"]["error"]["code"] == "RUNTIME_SAFE_MODE"
    assert recovery_begin["payload"]["result"]["status"] == "RESUME"
    assert checkpoints["payload"]["result"]["checkpoints"] == []
    assert recovery_complete["payload"]["result"] == {"status": "RESUME", "error_code": None}
    assert health_after_recovery["payload"]["result"]["status"] == "healthy"
    assert denied_unknown["payload"]["error"]["code"] == "IPC_METHOD_NOT_ALLOWED"
    assert "python-test-secret" not in process.stderr.read()


def test_main_sanitizes_unhandled_sidecar_exception_and_returns_failure(monkeypatch, caplog, tmp_path):
    import shuliu_runtime.ipc.server as module

    monkeypatch.setattr(module, "_runtime_data_directory", lambda: tmp_path)
    monkeypatch.setattr(module, "create_process_recovery_ipc_handlers", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(module, "create_provider_runtime_from_environment", lambda: object())

    class CrashingServer:
        def __init__(self, **_kwargs):
            pass

        def serve(self, output_stream=None):
            raise RuntimeError("Authorization: Bearer sk-private-marker C:\\Users\\private\\profile")

    monkeypatch.setattr(module, "IpcServer", CrashingServer)

    assert module.main() == 1
    error_output = caplog.text
    assert "event=runtime_unhandled_exception code=RUNTIME_UNHANDLED_EXCEPTION" in error_output
    assert "sk-private-marker" not in error_output
    assert "Users\\private" not in error_output
    assert "RuntimeError" not in error_output


def test_isolate_protocol_output_redirects_stray_prints_to_stderr(capfd):
    """A stray third-party print must never reach the protocol channel (IPC_JSON_INVALID)."""
    import shuliu_runtime.ipc.server as module

    original_stdout = sys.stdout
    protocol_stream = module._isolate_protocol_output()
    try:
        assert sys.stdout is sys.stderr
        print("STRAY-MARKER")
        protocol_stream.write("PROTOCOL-MARKER\n")
        protocol_stream.flush()
    finally:
        sys.stdout = original_stdout
        protocol_stream.close()

    captured = capfd.readouterr()
    assert "PROTOCOL-MARKER" in captured.out
    assert "STRAY-MARKER" in captured.err
    assert "STRAY-MARKER" not in captured.out


def test_main_serves_protocol_frames_on_isolated_stream(monkeypatch, capfd, tmp_path):
    import shuliu_runtime.ipc.server as module

    constructor_options = {}
    monkeypatch.setattr(module, "_runtime_data_directory", lambda: tmp_path)
    monkeypatch.setattr(module, "create_process_recovery_ipc_handlers", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(module, "create_provider_runtime_from_environment", lambda: object())

    class RecordingServer:
        def __init__(self, **kwargs):
            constructor_options.update(kwargs)

        def serve(self, output_stream):
            output_stream.write("PROTOCOL-MAIN-MARKER\n")
            output_stream.flush()

    monkeypatch.setattr(module, "IpcServer", RecordingServer)

    original_stdout = sys.stdout
    try:
        assert module.main() == 0
    finally:
        sys.stdout = original_stdout

    captured = capfd.readouterr()
    assert "PROTOCOL-MAIN-MARKER" in captured.out
    assert constructor_options["recovery_required"] is True


def test_runtime_instance_lock_is_acquired_before_database_bootstrap(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import shuliu_runtime.ipc.server as module

    calls = []

    class Lock:
        def acquire(self):
            calls.append("lock_acquired")

        def close(self):
            calls.append("lock_released")

    monkeypatch.setattr(module, "_runtime_data_directory", lambda: tmp_path)
    monkeypatch.setattr(
        module,
        "RuntimeInstanceLock",
        lambda path: (calls.append(f"lock_constructed:{path.name}") or Lock()),
    )
    monkeypatch.setattr(
        module,
        "bootstrap_runtime",
        lambda _path: (calls.append("database_bootstrap") or SimpleNamespace()),
    )
    monkeypatch.setattr(module, "_serve_bootstrapped_runtime", lambda _bootstrap: calls.append("serve") or 0)

    assert module.main() == 0
    assert calls == [
        "lock_constructed:runtime-instance.lock",
        "lock_acquired",
        "database_bootstrap",
        "serve",
        "lock_released",
    ]
