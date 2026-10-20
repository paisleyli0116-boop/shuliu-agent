from __future__ import annotations

import json
import logging
import sys
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO

from shuliu_runtime.ipc.bootstrap import RuntimeBootstrapError, bootstrap_runtime

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

logger = logging.getLogger("shuliu.runtime.ipc")
_MAX_FRAME_BYTES = 1_048_576
_MAX_CONCURRENT_REQUESTS = 32


class IpcProtocolError(ValueError):
    def __init__(self, code: str, path: str = "") -> None:
        super().__init__(f"runtime IPC frame is invalid ({code})")
        self.code = code
        self.path = path


class IpcRequestCancelled(Exception):
    pass


def _validator() -> Draft202012Validator:
    if getattr(sys, "frozen", False):
        schema_root = Path(getattr(sys, "_MEIPASS")) / "schemas"
    else:
        repository_root = Path(__file__).resolve().parents[4]
        schema_root = repository_root / "packages" / "protocols" / "schemas"
    common = json.loads((schema_root / "common-v1.schema.json").read_text(encoding="utf-8"))
    runtime_ipc = json.loads((schema_root / "runtime-ipc-v1.schema.json").read_text(encoding="utf-8"))
    registry = Registry().with_resource(common["$id"], Resource.from_contents(common))
    return Draft202012Validator(runtime_ipc, registry=registry)


_RUNTIME_IPC_VALIDATOR = _validator()


def validate_runtime_ipc_message(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise IpcProtocolError("SCHEMA_VALIDATION_FAILED", "/")
    version = value.get("protocol_version")
    if isinstance(version, int) and not isinstance(version, bool) and version != 1:
        raise IpcProtocolError("PROTOCOL_VERSION_UNSUPPORTED", "/protocol_version")
    errors = sorted(
        _RUNTIME_IPC_VALIDATOR.iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        path = "/" + "/".join(str(part) for part in errors[0].absolute_path)
        raise IpcProtocolError("SCHEMA_VALIDATION_FAILED", path)


RequestHandler = Callable[[Mapping[str, Any], threading.Event], Any]


def _error_payload(code: str, trace_id: str, category: str = "SYSTEM") -> dict[str, Any]:
    return {
        "code": code,
        "category": category,
        "message": "Runtime request could not be completed",
        "retryable": False,
        "recoverable": True,
        "severity": "ERROR",
        "details": {},
        "trace_id": trace_id,
    }


class IpcServer:
    """Serve versioned JSON lines; protocol data is written only to stdout."""

    def __init__(
        self,
        handlers: Mapping[str, RequestHandler] | None = None,
        *,
        max_concurrent_requests: int = _MAX_CONCURRENT_REQUESTS,
        health_state: Mapping[str, Any] | None = None,
    ) -> None:
        self._handlers = dict(handlers or {})
        self._health_state = dict(health_state or {"status": "healthy"})
        self._handlers.setdefault("runtime.handshake", self._handshake)
        self._handlers.setdefault("runtime.health", self._health)
        self._max_concurrent_requests = max(1, min(max_concurrent_requests, 128))

    @staticmethod
    def _handshake(_: Mapping[str, Any], __: threading.Event) -> dict[str, Any]:
        return {"protocol_version": 1, "status": "ready"}

    def _health(self, _: Mapping[str, Any], __: threading.Event) -> dict[str, Any]:
        return dict(self._health_state)

    def serve(
        self,
        input_stream: TextIO = sys.stdin,
        output_stream: TextIO = sys.stdout,
        error_stream: TextIO = sys.stderr,
    ) -> None:
        output_lock = threading.Lock()
        requests_lock = threading.Lock()
        cancellations: dict[str, threading.Event] = {}
        futures: set[Future[Any]] = set()
        executor = ThreadPoolExecutor(max_workers=self._max_concurrent_requests, thread_name_prefix="runtime-ipc")

        def write_frame(frame: Mapping[str, Any]) -> None:
            encoded = json.dumps(frame, separators=(",", ":"), ensure_ascii=False)
            with output_lock:
                output_stream.write(encoded + "\n")
                output_stream.flush()

        def respond(request: Mapping[str, Any], cancellation: threading.Event) -> None:
            request_id = str(request["request_id"])
            trace_id = str(request["trace_id"])
            request_payload = request["payload"]
            method = str(request_payload["method"])
            handler = self._handlers.get(method)
            if handler is None:
                result: dict[str, Any] = {"error": _error_payload("IPC_METHOD_NOT_ALLOWED", trace_id, "PERMISSION")}
            else:
                try:
                    value = handler(request_payload["params"], cancellation)
                    if cancellation.is_set():
                        raise IpcRequestCancelled
                    result = {"result": value if isinstance(value, dict) else {"value": value}}
                except IpcRequestCancelled:
                    result = {"error": _error_payload("IPC_REQUEST_CANCELLED", trace_id)}
                except Exception:
                    # Exception text and request payloads may contain secrets; keep them out of logs and frames.
                    logger.error("event=runtime_request_failed code=IPC_REQUEST_FAILED")
                    result = {"error": _error_payload("IPC_REQUEST_FAILED", trace_id)}
            write_frame(
                {
                    "protocol_name": "runtime-ipc",
                    "protocol_version": 1,
                    "message_id": str(uuid.uuid4()),
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "message_type": "response",
                    "payload": result,
                }
            )
            with requests_lock:
                cancellations.pop(request_id, None)

        try:
            for raw_line in input_stream:
                if len(raw_line.encode("utf-8")) > _MAX_FRAME_BYTES:
                    logger.error("event=runtime_frame_rejected code=IPC_FRAME_TOO_LARGE")
                    continue
                try:
                    request = json.loads(raw_line)
                    validate_runtime_ipc_message(request)
                except (json.JSONDecodeError, IpcProtocolError):
                    logger.error("event=runtime_frame_rejected code=IPC_FRAME_INVALID")
                    continue

                message_type = request["message_type"]
                request_id = request["request_id"]
                if message_type == "cancel":
                    with requests_lock:
                        cancellation = cancellations.get(request_id)
                    if cancellation is not None:
                        cancellation.set()
                    continue
                if message_type != "request":
                    logger.error("event=runtime_frame_rejected code=IPC_DIRECTION_INVALID")
                    continue

                cancellation = threading.Event()
                with requests_lock:
                    cancellations[request_id] = cancellation
                future = executor.submit(respond, request, cancellation)
                futures.add(future)
                future.add_done_callback(futures.discard)
        finally:
            executor.shutdown(wait=True, cancel_futures=False)


def _runtime_data_directory() -> Path:
    configured = __import__("os").environ.get("SHULIU_DATA_DIR")
    if configured:
        return Path(configured)
    local_app_data = __import__("os").environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ShuLiu Agent" / "User Data"
    return Path.home() / ".local" / "share" / "shuliu-agent"


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
    try:
        bootstrap = bootstrap_runtime(_runtime_data_directory())
    except RuntimeBootstrapError as error:
        logger.error("event=runtime_bootstrap_failed code=%s", error.code)
        return 1

    health_state = {
        "status": "healthy",
        "components": {
            "database": {
                "status": "healthy",
                "schema_version": bootstrap.database_schema,
            },
        },
    }
    IpcServer(health_state=health_state).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
