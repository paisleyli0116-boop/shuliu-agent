from __future__ import annotations

import json
import logging
import os
import sys
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO

from shuliu_runtime.ipc.bootstrap import RuntimeBootstrapError, bootstrap_runtime
from shuliu_runtime.ipc.recovery import create_process_recovery_ipc_handlers
from shuliu_runtime.ipc.runtime_instance_lock import RuntimeInstanceAlreadyActive, RuntimeInstanceLock
from shuliu_runtime.models.config import ProviderConfigurationError
from shuliu_runtime.models.connection import create_provider_ipc_handlers, create_provider_runtime_from_environment
from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.production.composition import (
    attach_workflow_runtime,
    compose_production_dependencies,
)
from shuliu_runtime.recovery.sqlite_safe_mode import SQLiteSafeModeController

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

logger = logging.getLogger("shuliu.runtime.ipc")
_MAX_FRAME_BYTES = 1_048_576
_MAX_CONCURRENT_REQUESTS = 32
_SAFE_MODE_DIAGNOSTIC_METHODS = frozenset({
    "runtime.handshake",
    "runtime.health",
    "runtime.recovery.checkpoints",
})
_SAFE_MODE_RECOVERY_METHODS = frozenset({
    "runtime.recovery.begin",
    "runtime.recovery.recover_run",
    "runtime.recovery.complete",
})


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
        safe_mode: bool = False,
        safe_mode_error_code: str = "RUNTIME_SAFE_MODE",
        recovery_required: bool = False,
    ) -> None:
        if not isinstance(recovery_required, bool):
            raise TypeError("recovery_required must be a boolean")
        self._handlers = dict(handlers or {})
        self._health_state = dict(health_state or {"status": "healthy"})
        self._safe_mode = safe_mode is True or recovery_required
        self._safe_mode_error_code = (
            safe_mode_error_code
            if safe_mode_error_code in {"RUNTIME_SAFE_MODE", "RUNTIME_SAFE_MODE_STATE_INVALID"}
            else "RUNTIME_SAFE_MODE"
        )
        self._safe_mode_lock = threading.RLock()
        if self._safe_mode:
            self._health_state["status"] = "safe_mode"
            self._health_state["safe_error_code"] = self._safe_mode_error_code
        self._handlers.setdefault("runtime.handshake", self._handshake)
        self._handlers.setdefault("runtime.health", self._health)
        self._max_concurrent_requests = max(1, min(max_concurrent_requests, 128))

    @staticmethod
    def _handshake(_: Mapping[str, Any], __: threading.Event) -> dict[str, Any]:
        return {"protocol_version": 1, "status": "ready"}

    def _health(self, _: Mapping[str, Any], __: threading.Event) -> dict[str, Any]:
        with self._safe_mode_lock:
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
            with self._safe_mode_lock:
                safe_mode_denies_request = self._safe_mode and method not in (
                    _SAFE_MODE_DIAGNOSTIC_METHODS | _SAFE_MODE_RECOVERY_METHODS
                )
            if safe_mode_denies_request:
                result: dict[str, Any] = {
                    "error": _error_payload(self._safe_mode_error_code, trace_id, "PERMISSION")
                }
            elif handler is None:
                result: dict[str, Any] = {"error": _error_payload("IPC_METHOD_NOT_ALLOWED", trace_id, "PERMISSION")}
            else:
                try:
                    value = handler(request_payload["params"], cancellation)
                    # A successful recovery release is the commit point: its
                    # durable Safe Mode marker and audit event have already
                    # changed atomically. A cancellation arriving after that
                    # point cannot undo the commit, so report it and bring the
                    # in-memory gate into agreement with durable state.
                    recovery_release_committed = (
                        method == "runtime.recovery.complete"
                        and isinstance(value, Mapping)
                        and value.get("status") == "RESUME"
                        and value.get("error_code") is None
                    )
                    if cancellation.is_set() and not recovery_release_committed:
                        raise IpcRequestCancelled
                    result = {"result": value if isinstance(value, dict) else {"value": value}}
                    recovery_requires_safe_mode = (
                        method in _SAFE_MODE_RECOVERY_METHODS
                        and isinstance(value, Mapping)
                        and value.get("status") in {"WAIT", "SAFE_MODE", "DEAD_LETTER"}
                    )
                    if recovery_requires_safe_mode:
                        with self._safe_mode_lock:
                            self._safe_mode = True
                            self._safe_mode_error_code = "RUNTIME_SAFE_MODE"
                            self._health_state["status"] = "safe_mode"
                            self._health_state["safe_error_code"] = "RUNTIME_SAFE_MODE"
                    if recovery_release_committed:
                        with self._safe_mode_lock:
                            self._safe_mode = False
                            self._health_state["status"] = "healthy"
                            self._health_state.pop("safe_error_code", None)
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


def _isolate_protocol_output() -> TextIO:
    """Reserve the process stdout channel for protocol frames only.

    Third-party libraries (e.g. litellm) can print log lines to sys.stdout at
    any time; one stray line corrupts the NDJSON channel and forces the Desktop
    supervisor into Safe Mode (IPC_JSON_INVALID). The protocol writer therefore
    gets a private duplicate of file descriptor 1, and sys.stdout is redirected
    to sys.stderr so stray prints are captured by the safe sidecar log path.
    """
    protocol_stream = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr
    return protocol_stream


def _serve_bootstrapped_runtime(bootstrap: Any) -> int:
    workflow_unavailable_code = getattr(bootstrap, "workflow_unavailable_code", None)
    persisted_safe_mode = SQLiteSafeModeController(bootstrap.database).status()

    health_state = {
        "status": "safe_mode" if persisted_safe_mode.active else "healthy",
        "provider_connection_test_enabled": os.environ.get("SHULIU_MODEL_CONNECTION_TEST") == "1",
        "components": {
            "database": {
                "status": "healthy",
                "schema_version": bootstrap.database_schema,
            },
        },
    }
    if persisted_safe_mode.active:
        health_state["safe_error_code"] = persisted_safe_mode.error_code or "RUNTIME_SAFE_MODE"
    try:
        provider_runtime = create_provider_runtime_from_environment()
    except ProviderConfigurationError:
        logger.error("event=provider_configuration_failed code=MODEL_PROVIDER_CONFIGURATION_INVALID")
        return 1

    try:
        handlers = create_provider_ipc_handlers(provider_runtime)
        runtime_database = getattr(bootstrap, "database", None)
        if runtime_database is None:
            runtime_database = SQLiteDatabase(bootstrap.database_path)
        handlers.update(create_process_recovery_ipc_handlers(
            runtime_database,
            expected_context_version=os.environ.get(
                "SHULIU_WORKFLOW_CONTEXT_VERSION", "workflow:v1;skill:v1"
            ),
        ))
        workflow_runtime = getattr(bootstrap, "workflow_runtime", None)
        if workflow_runtime is not None:
            handlers.update(workflow_runtime.create_ipc_handlers())
            health_state["components"]["workflow"] = {"status": "healthy"}
        else:
            health_state["components"]["workflow"] = {
                "status": "not_configured",
                "error_code": workflow_unavailable_code
                or "WORKFLOW_RUNTIME_NOT_CONFIGURED",
            }
        IpcServer(
            handlers=handlers,
            health_state=health_state,
            safe_mode=persisted_safe_mode.active,
            safe_mode_error_code=persisted_safe_mode.error_code or "RUNTIME_SAFE_MODE",
            recovery_required=True,
        ).serve(output_stream=_isolate_protocol_output())
    except Exception:
        # Preserve a safe crash marker for the Desktop supervisor. Never emit
        # exception text, tracebacks, request data, or credential material.
        logger.error("event=runtime_unhandled_exception code=RUNTIME_UNHANDLED_EXCEPTION")
        return 1
    return 0


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
    data_directory = Path(_runtime_data_directory())
    lock = RuntimeInstanceLock(data_directory / "runtime-instance.lock")
    try:
        lock.acquire()
    except RuntimeInstanceAlreadyActive:
        logger.error("event=runtime_instance_lock_failed code=RUNTIME_INSTANCE_ALREADY_ACTIVE")
        return 1
    try:
        try:
            bootstrap = bootstrap_runtime(data_directory)
        except RuntimeBootstrapError as error:
            logger.error("event=runtime_bootstrap_failed code=%s", error.code)
            return 1
        bootstrap = _attach_production_workflow(bootstrap)
        return _serve_bootstrapped_runtime(bootstrap)
    finally:
        lock.close()


def _attach_production_workflow(bootstrap: Any) -> Any:
    """Compose the trusted Workflow runtime from deployment configuration.

    The Sidecar starts either way: a missing or invalid trusted dependency keeps
    Workflow switched off and reported as `not_configured`, rather than opening a
    permissive path or failing the whole application.
    """
    try:
        outcome = compose_production_dependencies(bootstrap.database, os.environ)
        return attach_workflow_runtime(
            bootstrap, outcome.dependencies, outcome.unavailable_code
        )
    except Exception:
        # 依赖已配置但装配失败时同样保持关闭，并留下稳定错误码供排障。
        logger.error("event=workflow_composition_failed code=WORKFLOW_COMPOSITION_FAILED")
        return _mark_workflow_unavailable(bootstrap, "WORKFLOW_COMPOSITION_FAILED")


def _mark_workflow_unavailable(bootstrap: Any, code: str) -> Any:
    try:
        return replace(bootstrap, workflow_unavailable_code=code)
    except Exception:
        # 非标准引导结果（测试替身）没有可替换字段，保持原样即可。
        return bootstrap


if __name__ == "__main__":
    raise SystemExit(main())
