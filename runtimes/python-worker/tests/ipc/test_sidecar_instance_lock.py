from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from shuliu_runtime.ipc.runtime_instance_lock import RuntimeInstanceLock


def _start_sidecar(data_directory: Path):
    repository = Path(__file__).resolve().parents[4]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repository / "runtimes" / "python-worker")
    environment["SHULIU_DATA_DIR"] = str(data_directory)
    environment["SHULIU_MODEL_CONNECTION_TEST"] = "0"
    process = subprocess.Popen(
        [sys.executable, "-m", "shuliu_runtime.ipc.server"],
        cwd=repository,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    assert process.stdout is not None
    output: queue.Queue[str | None] = queue.Queue()

    def read_lines() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output.put(line)
        output.put(None)

    threading.Thread(target=read_lines, daemon=True).start()
    return process, output


def _request_health(process, output, request_id):
    assert process.stdin is not None
    request = {
        "protocol_name": "runtime-ipc",
        "protocol_version": 1,
        "message_id": f"message-{request_id}",
        "request_id": request_id,
        "trace_id": "trace-instance-lock",
        "message_type": "request",
        "payload": {"method": "runtime.health", "params": {}},
    }
    process.stdin.write(__import__("json").dumps(request) + "\n")
    process.stdin.flush()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            line = output.get(timeout=0.1)
        except queue.Empty:
            if process.poll() is not None:
                raise AssertionError("Sidecar exited before health response")
            continue
        assert line is not None
        response = __import__("json").loads(line)
        if response.get("request_id") == request_id:
            return response["payload"]["result"]
    raise AssertionError("Sidecar health response timed out")


def test_second_sidecar_for_same_user_data_fails_closed_until_first_exits(tmp_path):
    first, first_output = _start_sidecar(tmp_path)
    second = None
    third = None
    try:
        # A newly booted production Sidecar remains fail-closed until the
        # Electron startup recovery handshake completes. It still owns the
        # instance lock while in Safe Mode.
        assert _request_health(first, first_output, "first-health")["status"] == "safe_mode"
        second = subprocess.run(
            [sys.executable, "-m", "shuliu_runtime.ipc.server"],
            cwd=Path(__file__).resolve().parents[4],
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).resolve().parents[4] / "runtimes" / "python-worker"),
                "SHULIU_DATA_DIR": str(tmp_path),
                "SHULIU_MODEL_CONNECTION_TEST": "0",
            },
            input="",
            text=True,
            capture_output=True,
            timeout=5,
        )
        assert second.returncode == 1
        assert "code=RUNTIME_INSTANCE_ALREADY_ACTIVE" in second.stderr

        assert first.stdin is not None
        first.stdin.close()
        first.wait(timeout=5)
        third, third_output = _start_sidecar(tmp_path)
        assert _request_health(third, third_output, "third-health")["status"] == "safe_mode"
    finally:
        for process in (first, third):
            if process is not None and process.poll() is None:
                if process.stdin is not None:
                    process.stdin.close()
                process.wait(timeout=5)
