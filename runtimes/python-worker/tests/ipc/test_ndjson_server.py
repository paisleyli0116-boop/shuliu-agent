from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from shuliu_runtime.ipc.server import IpcProtocolError, validate_runtime_ipc_message


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


def test_python_sidecar_reads_ndjson_and_writes_only_protocol_frames_to_stdout() -> None:
    repository = Path(__file__).resolve().parents[4]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repository / "runtimes" / "python-worker")
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
    process.stdin.write(json.dumps(_message("request", {"method": "runtime.handshake", "params": {}})) + "\n")
    process.stdin.write(json.dumps(_message("request", {"method": "runtime.health", "params": {}}, "request-2")) + "\n")
    process.stdin.write(json.dumps(_message("request", {"method": "tool.execute", "params": {}}, "request-3")) + "\n")
    process.stdin.write('{"authorization":"python-test-secret"\n')
    process.stdin.flush()
    responses = [json.loads(process.stdout.readline()) for _ in range(3)]
    process.stdin.close()
    process.wait(timeout=5)

    assert {item["request_id"] for item in responses} == {"request-1", "request-2", "request-3"}
    assert all(item["message_type"] == "response" for item in responses)
    assert all(item["protocol_name"] == "runtime-ipc" and item["protocol_version"] == 1 for item in responses)
    denied = next(item for item in responses if item["request_id"] == "request-3")
    assert denied["payload"]["error"]["code"] == "IPC_METHOD_NOT_ALLOWED"
    assert "python-test-secret" not in process.stderr.read()


def test_main_sanitizes_unhandled_sidecar_exception_and_returns_failure(monkeypatch, caplog):
    from types import SimpleNamespace
    import shuliu_runtime.ipc.server as module

    monkeypatch.setattr(module, "bootstrap_runtime", lambda _path: SimpleNamespace(database_schema="v1"))
    monkeypatch.setattr(module, "create_provider_runtime_from_environment", lambda: object())

    class CrashingServer:
        def __init__(self, **_kwargs):
            pass

        def serve(self):
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


def test_main_serves_protocol_frames_on_isolated_stream(monkeypatch, capfd):
    from types import SimpleNamespace
    import shuliu_runtime.ipc.server as module

    monkeypatch.setattr(module, "bootstrap_runtime", lambda _path: SimpleNamespace(database_schema="v1"))
    monkeypatch.setattr(module, "create_provider_runtime_from_environment", lambda: object())

    class RecordingServer:
        def __init__(self, **_kwargs):
            pass

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
