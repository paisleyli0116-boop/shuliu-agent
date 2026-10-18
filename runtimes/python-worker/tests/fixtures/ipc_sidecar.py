from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any, Mapping

from shuliu_runtime.ipc.server import IpcRequestCancelled, IpcServer


def _echo(params: Mapping[str, Any], _: threading.Event) -> dict[str, Any]:
    return {"value": params.get("value")}


def _delay(params: Mapping[str, Any], cancelled: threading.Event) -> dict[str, Any]:
    if cancelled.wait(float(params.get("seconds", 0.25))):
        raise IpcRequestCancelled
    return {"status": "finished"}


def _crash(_: Mapping[str, Any], __: threading.Event) -> dict[str, Any]:
    os._exit(23)


def _environment(_: Mapping[str, Any], __: threading.Event) -> dict[str, Any]:
    return {
        "sensitive_variable_present": "SHULIU_SECRET_SENTINEL" in os.environ,
        "stdin_encoding": sys.stdin.encoding.lower(),
        "stdout_encoding": sys.stdout.encoding.lower(),
    }


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "incompatible":
        request = json.loads(sys.stdin.readline())
        response = {
            "protocol_name": "runtime-ipc",
            "protocol_version": 2,
            "message_id": "incompatible-message",
            "request_id": request["request_id"],
            "trace_id": request["trace_id"],
            "message_type": "response",
            "payload": {"result": {"protocol_version": 2, "status": "ready"}},
        }
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
        return

    # The supervisor must not forward arbitrary stderr fields into normal logs.
    sys.stderr.write('{"level":"INFO","event":"fixture_start","code":"IPC_READY","authorization":"fixture-secret-marker"}\n')
    sys.stderr.flush()
    IpcServer({
        "test.echo": _echo,
        "test.delay": _delay,
        "test.crash": _crash,
        "test.environment": _environment,
    }).serve()


if __name__ == "__main__":
    main()
