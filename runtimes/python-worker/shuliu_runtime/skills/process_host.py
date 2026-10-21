from __future__ import annotations

import json
import math
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .capability_proxy import SkillCapabilityProxy
from .sandbox import (
    REQUIRED_SANDBOX_CONTROLS,
    SandboxAssessment,
    SandboxPolicy,
    SandboxProvider,
    SandboxUnavailableError,
    UnavailableSandboxProvider,
)

_MAX_FRAME_BYTES = 64 * 1024
_MAX_INPUT_BYTES = 1024 * 1024
_MAX_PENDING_TOOL_CALLS = 8
_SANDBOX_LAUNCH_TOKEN = object()
_TOOL_DISPATCH_SLOTS = threading.BoundedSemaphore(_MAX_PENDING_TOOL_CALLS)
_WINDOWS_CREATE_SUSPENDED = 0x00000004
_WINDOWS_JOB_CLEANUP_TIMEOUT_SECONDS = 5.0


class SkillProcessError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        return_code: int | None = None,
        working_directory: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.return_code = return_code
        self.working_directory = working_directory


@dataclass(frozen=True, slots=True)
class SkillProcessResult:
    output: object
    process_id: int


class SkillProcessHost:
    """Runs verified Skill entrypoints only when a provider proves OS controls."""

    def __init__(self, sandbox_provider: SandboxProvider | None = None) -> None:
        self._sandbox_provider = sandbox_provider or UnavailableSandboxProvider()

    def run(
        self,
        package_path: str | Path,
        policy: SandboxPolicy,
        input_value: object,
        *,
        timeout: float,
        entrypoint: str = "main.py",
        capability_proxy: SkillCapabilityProxy | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SkillProcessResult:
        try:
            package = Path(package_path).resolve(strict=True)
        except (OSError, TypeError) as error:
            raise SkillProcessError(
                "SKILL_PACKAGE_INVALID", "Skill package cannot be resolved"
            ) from error
        if not package.is_dir():
            raise SkillProcessError(
                "SKILL_PACKAGE_INVALID", "Skill package must be a directory"
            )
        script = _resolve_entrypoint(package, entrypoint)
        _validate_filesystem_roots(package, policy)
        _validate_timeout(timeout)
        if capability_proxy is not None and not isinstance(
            capability_proxy, SkillCapabilityProxy
        ):
            raise SandboxUnavailableError(
                "SKILL_TOOL_BROKER_UNAVAILABLE",
                "Skill Tool requests require the Foundation capability proxy",
            )
        if (
            capability_proxy is not None
            and not capability_proxy.approved_permissions.issubset(
                frozenset(policy.permissions)
            )
        ):
            raise SandboxUnavailableError(
                "SKILL_PERMISSION_ESCALATION",
                "Tool proxy permissions exceed the approved Skill policy",
            )

        try:
            assessment = self._sandbox_provider.assess(policy)
        except SandboxUnavailableError:
            raise
        except Exception as error:
            raise SandboxUnavailableError(
                "SKILL_SANDBOX_UNAVAILABLE", "Sandbox provider assessment failed"
            ) from error
        if not isinstance(assessment, SandboxAssessment):
            raise SandboxUnavailableError(
                "SKILL_SANDBOX_UNAVAILABLE",
                "Sandbox provider returned an invalid assessment",
            )
        self._require_enforced_controls(
            supported=assessment.supported,
            enforced_controls=assessment.enforced_controls,
        )

        with tempfile.TemporaryDirectory(prefix="shuliu-skill-") as temp_name:
            working_directory = Path(temp_name)
            _restrict_directory_permissions(working_directory)
            command = [sys.executable, "-I", str(script)]
            try:
                wrapped = self._sandbox_provider.wrap_command(
                    command, policy, working_directory
                )
            except SandboxUnavailableError:
                raise
            except Exception as error:
                raise SandboxUnavailableError(
                    "SKILL_SANDBOX_UNAVAILABLE", "Sandbox command preparation failed"
                ) from error
            if (
                isinstance(wrapped, (str, bytes))
                or not isinstance(wrapped, Sequence)
                or not wrapped
                or any(not isinstance(part, str) or not part for part in wrapped)
            ):
                raise SandboxUnavailableError(
                    "SKILL_SANDBOX_UNAVAILABLE",
                    "Sandbox provider returned an invalid command",
                )
            return _run_supervised_process(
                input_value,
                timeout=timeout,
                capability_proxy=capability_proxy,
                cancel_event=cancel_event,
                command=wrapped,
                working_directory=working_directory,
                _launch_token=_SANDBOX_LAUNCH_TOKEN,
            )

    @staticmethod
    def _require_enforced_controls(
        *, supported: bool, enforced_controls: frozenset[str]
    ) -> None:
        if not supported or not REQUIRED_SANDBOX_CONTROLS.issubset(enforced_controls):
            missing = REQUIRED_SANDBOX_CONTROLS.difference(enforced_controls)
            detail = ", ".join(sorted(missing)) or "provider unavailable"
            raise SandboxUnavailableError(
                "SKILL_SANDBOX_UNAVAILABLE",
                f"Required OS sandbox controls are unavailable: {detail}",
            )


def _run_supervised_process(
    input_value: object,
    *,
    timeout: float,
    command: Sequence[str],
    working_directory: Path,
    _launch_token: object,
    capability_proxy: SkillCapabilityProxy | None = None,
    cancel_event: threading.Event | None = None,
) -> SkillProcessResult:
    if _launch_token is not _SANDBOX_LAUNCH_TOKEN:
        raise SkillProcessError(
            "SKILL_SANDBOX_UNAVAILABLE",
            "Process supervisor can only be called through the validated host",
        )
    _validate_timeout(timeout)
    encoded_input = _encode_frame({"type": "skill.input", "input": input_value})
    cwd = working_directory
    child_env = _minimal_environment(cwd)
    child_command = list(command)
    process: subprocess.Popen[bytes] | None = None
    job_handle: Any = None
    stdout_queue: queue.Queue[bytes | None | BaseException] = queue.Queue(maxsize=32)
    reader_stop = threading.Event()
    writer_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=8)
    writer_errors: queue.Queue[BaseException] = queue.Queue(maxsize=1)
    tool_results: queue.Queue[tuple[str, dict[str, object]]] = queue.Queue(maxsize=1)
    deadline = time.monotonic() + timeout
    stderr_thread: threading.Thread | None = None
    stdout_thread: threading.Thread | None = None
    stdin_thread: threading.Thread | None = None
    pending_tool_request: str | None = None
    try:
        _restrict_directory_permissions(cwd)
        process = _spawn(child_command, cwd, child_env)
        if os.name == "nt":
            job_handle = _attach_windows_job(process)
            _resume_windows_process(process.pid)
        stdout_thread = threading.Thread(
            target=_read_stdout,
            args=(process.stdout, stdout_queue, reader_stop),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_drain_stderr, args=(process.stderr,), daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()
        assert process.stdin is not None
        stdin_thread = threading.Thread(
            target=_write_stdin,
            args=(process.stdin.fileno(), writer_queue, writer_errors),
            daemon=True,
        )
        stdin_thread.start()
        writer_queue.put_nowait(encoded_input)
        output: object | None = None
        saw_result = False
        while not saw_result:
            if pending_tool_request is not None:
                try:
                    request_id, response = tool_results.get_nowait()
                except queue.Empty:
                    pass
                else:
                    if request_id != pending_tool_request:
                        raise SkillProcessError(
                            "SKILL_PROCESS_PROTOCOL_ERROR",
                            "Tool Broker response does not match its request",
                            working_directory=cwd,
                        )
                    writer_queue.put_nowait(_encode_frame(response))
                    pending_tool_request = None
            try:
                writer_error = writer_errors.get_nowait()
            except queue.Empty:
                pass
            else:
                raise SkillProcessError(
                    "SKILL_PROCESS_IO_ERROR",
                    "Skill input channel failed",
                    working_directory=cwd,
                ) from writer_error
            if cancel_event is not None and cancel_event.is_set():
                if pending_tool_request is not None:
                    raise SkillProcessError(
                        "SKILL_TOOL_EXECUTION_UNKNOWN",
                        "Tool execution may still be running; reconcile before retrying",
                        working_directory=cwd,
                    )
                raise SkillProcessError(
                    "SKILL_EXECUTION_CANCELLED",
                    "Skill execution was cancelled",
                    working_directory=cwd,
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if pending_tool_request is not None:
                    raise SkillProcessError(
                        "SKILL_TOOL_EXECUTION_UNKNOWN",
                        "Tool execution may still be running; reconcile before retrying",
                        working_directory=cwd,
                    )
                raise SkillProcessError(
                    "SKILL_EXECUTION_TIMEOUT",
                    "Skill execution exceeded its timeout",
                    working_directory=cwd,
                )
            try:
                item = stdout_queue.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                if process.poll() is not None:
                    return_code = process.wait()
                    code = (
                        "SKILL_PROCESS_CRASHED"
                        if _is_crash(return_code)
                        else "SKILL_PROCESS_EXITED"
                    )
                    raise SkillProcessError(
                        code,
                        "Skill process exited before returning a result",
                        return_code=return_code,
                        working_directory=cwd,
                    )
                continue
            if item is None:
                return_code = process.wait(
                    timeout=max(0.01, deadline - time.monotonic())
                )
                code = (
                    "SKILL_PROCESS_CRASHED"
                    if _is_crash(return_code)
                    else "SKILL_PROCESS_EXITED"
                )
                raise SkillProcessError(
                    code,
                    "Skill process exited without a result frame",
                    return_code=return_code,
                    working_directory=cwd,
                )
            if isinstance(item, BaseException):
                raise SkillProcessError(
                    "SKILL_PROCESS_PROTOCOL_ERROR",
                    "Skill emitted an invalid or oversized protocol frame",
                    working_directory=cwd,
                ) from item
            frame = _decode_frame(item, cwd)
            frame_type = frame.get("type")
            if frame_type == "tool.request":
                request_id = frame.get("request_id")
                if not isinstance(request_id, str) or not request_id:
                    raise SkillProcessError(
                        "SKILL_PROCESS_PROTOCOL_ERROR",
                        "Tool request has no valid request ID",
                        working_directory=cwd,
                    )
                if pending_tool_request is not None:
                    raise SkillProcessError(
                        "SKILL_PROCESS_PROTOCOL_ERROR",
                        "Skill issued concurrent Tool requests",
                        working_directory=cwd,
                    )
                if capability_proxy is None:
                    response = {
                        "type": "tool.response",
                        "request_id": request_id,
                        "error": {"code": "SKILL_TOOL_BROKER_UNAVAILABLE"},
                    }
                    writer_queue.put_nowait(_encode_frame(response))
                elif not _TOOL_DISPATCH_SLOTS.acquire(blocking=False):
                    response = {
                        "type": "tool.response",
                        "request_id": request_id,
                        "error": {"code": "TOOL_BROKER_BUSY"},
                    }
                    writer_queue.put_nowait(_encode_frame(response))
                else:
                    pending_tool_request = request_id
                    dispatcher = threading.Thread(
                        target=_dispatch_tool_request,
                        args=(capability_proxy, frame, tool_results),
                        daemon=True,
                    )
                    try:
                        dispatcher.start()
                    except RuntimeError:
                        pending_tool_request = None
                        _TOOL_DISPATCH_SLOTS.release()
                        raise
            elif frame_type == "skill.progress":
                continue
            elif frame_type == "skill.result":
                if set(frame) != {"type", "output"}:
                    raise SkillProcessError(
                        "SKILL_PROCESS_PROTOCOL_ERROR",
                        "Skill result frame has unsupported fields",
                        working_directory=cwd,
                    )
                output = frame["output"]
                saw_result = True
            elif frame_type == "skill.error":
                raise SkillProcessError(
                    "SKILL_EXECUTION_FAILED",
                    "Skill reported an execution error",
                    working_directory=cwd,
                )
            else:
                raise SkillProcessError(
                    "SKILL_PROCESS_PROTOCOL_ERROR",
                    "Skill emitted an unsupported protocol frame",
                    working_directory=cwd,
                )

        try:
            writer_queue.put_nowait(None)
        except queue.Full:
            raise SkillProcessError(
                "SKILL_PROCESS_IO_ERROR",
                "Skill input channel could not be closed",
                working_directory=cwd,
            )
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise SkillProcessError(
                    "SKILL_EXECUTION_CANCELLED",
                    "Skill execution was cancelled",
                    working_directory=cwd,
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SkillProcessError(
                    "SKILL_EXECUTION_TIMEOUT",
                    "Skill did not exit after returning a result",
                    working_directory=cwd,
                )
            try:
                return_code = process.wait(timeout=min(remaining, 0.05))
                break
            except subprocess.TimeoutExpired:
                continue
        if return_code != 0:
            raise SkillProcessError(
                "SKILL_PROCESS_EXITED",
                "Skill returned a result but exited abnormally",
                return_code=return_code,
                working_directory=cwd,
            )
        return SkillProcessResult(output=output, process_id=process.pid)
    except subprocess.TimeoutExpired as error:
        raise SkillProcessError(
            "SKILL_EXECUTION_TIMEOUT",
            "Skill execution exceeded its timeout",
            working_directory=cwd,
        ) from error
    finally:
        reader_stop.set()
        cleanup_error: Exception | None = None
        if process is not None:
            try:
                _terminate_process_tree(process, job_handle)
            except Exception as error:  # noqa: BLE001 - cleanup failure is a hard error
                cleanup_error = error
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    try:
                        stream.close()
                    except OSError as error:
                        cleanup_error = cleanup_error or error
        elif job_handle is not None:
            try:
                _close_windows_handle(job_handle)
            except Exception as error:  # noqa: BLE001 - cleanup failure is a hard error
                cleanup_error = error
        for thread in (stdin_thread, stdout_thread, stderr_thread):
            if thread is not None:
                thread.join(timeout=0.5)
        if cleanup_error is not None:
            raise SkillProcessError(
                "SKILL_RESOURCE_CLEANUP_FAILED",
                "Skill process or temporary resources could not be cleaned up",
                working_directory=cwd,
            ) from cleanup_error


def _resolve_entrypoint(package: Path, entrypoint: str) -> Path:
    if not isinstance(entrypoint, str) or not entrypoint.strip():
        raise SkillProcessError(
            "SKILL_ENTRYPOINT_INVALID", "Skill entrypoint is required"
        )
    relative = Path(entrypoint)
    if relative.is_absolute() or relative.suffix != ".py":
        raise SkillProcessError(
            "SKILL_ENTRYPOINT_INVALID",
            "Skill entrypoint must be a relative Python file",
        )
    current = package
    for part in relative.parts:
        if part in ("", ".", ".."):
            raise SkillProcessError(
                "SKILL_ENTRYPOINT_INVALID", "Skill entrypoint escaped its package"
            )
        current = current / part
        if current.is_symlink():
            raise SkillProcessError(
                "SKILL_ENTRYPOINT_INVALID", "Skill entrypoint cannot contain symlinks"
            )
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(package)
    except (OSError, ValueError) as error:
        raise SkillProcessError(
            "SKILL_ENTRYPOINT_INVALID",
            "Skill entrypoint is outside its verified package",
        ) from error
    if not resolved.is_file():
        raise SkillProcessError(
            "SKILL_ENTRYPOINT_INVALID", "Skill entrypoint must be a regular file"
        )
    return resolved


def _validate_filesystem_roots(package: Path, policy: SandboxPolicy) -> None:
    for root in policy.filesystem_roots:
        try:
            resolved = Path(root).resolve(strict=True)
            resolved.relative_to(package)
        except (OSError, ValueError) as error:
            raise SandboxUnavailableError(
                "SKILL_FOUNDATION_DATA_ACCESS_DENIED",
                "Skill filesystem policy may grant access only within its verified package",
            ) from error


def _validate_timeout(timeout: float) -> None:
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (float, int))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise SkillProcessError(
            "SKILL_TIMEOUT_INVALID", "Skill timeout must be positive"
        )


def _minimal_environment(working_directory: Path) -> dict[str, str]:
    return {
        "PATH": os.defpath,
        "HOME": str(working_directory),
        "TMPDIR": str(working_directory),
        "TMP": str(working_directory),
        "TEMP": str(working_directory),
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
    }


def _encode_frame(frame: Mapping[str, object]) -> bytes:
    try:
        content = json.dumps(
            frame, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise SkillProcessError(
            "SKILL_PROCESS_PROTOCOL_ERROR", "Skill IPC frame is not JSON serializable"
        ) from error
    if len(content) > _MAX_INPUT_BYTES:
        raise SkillProcessError(
            "SKILL_INPUT_TOO_LARGE", "Skill IPC frame exceeds the configured size limit"
        )
    return content + b"\n"


def _decode_frame(raw: bytes, working_directory: Path) -> dict[str, object]:
    if len(raw) > _MAX_FRAME_BYTES or not raw.endswith(b"\n"):
        raise SkillProcessError(
            "SKILL_PROCESS_PROTOCOL_ERROR",
            "Skill IPC frame is incomplete or exceeds the size limit",
            working_directory=working_directory,
        )
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
    except (UnicodeError, ValueError) as error:
        raise SkillProcessError(
            "SKILL_PROCESS_PROTOCOL_ERROR",
            "Skill stdout must contain only NDJSON frames",
            working_directory=working_directory,
        ) from error
    if not isinstance(value, dict):
        raise SkillProcessError(
            "SKILL_PROCESS_PROTOCOL_ERROR",
            "Skill IPC frame must be a JSON object",
            working_directory=working_directory,
        )
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"unsupported JSON constant {value}")


def _write_stdin(
    file_descriptor: int,
    frames: queue.Queue[bytes | None],
    errors: queue.Queue[BaseException],
) -> None:
    try:
        while True:
            frame = frames.get()
            if frame is None:
                return
            remaining = memoryview(frame)
            while remaining:
                written = os.write(file_descriptor, remaining)
                if written <= 0:
                    raise OSError("Skill stdin write made no progress")
                remaining = remaining[written:]
    except BaseException as error:  # noqa: BLE001 - surface pipe failure to host
        try:
            errors.put_nowait(error)
        except queue.Full:
            pass


def _dispatch_tool_request(
    proxy: SkillCapabilityProxy,
    frame: Mapping[str, object],
    results: queue.Queue[tuple[str, dict[str, object]]],
) -> None:
    request_id = frame.get("request_id")
    assert isinstance(request_id, str)
    try:
        try:
            response = proxy.dispatch(frame)
        except Exception:  # noqa: BLE001 - proxy failure fails closed
            response = {
                "type": "tool.response",
                "request_id": request_id,
                "error": {"code": "SKILL_TOOL_REQUEST_INVALID"},
            }
        try:
            results.put_nowait((request_id, response))
        except queue.Full:
            return
    finally:
        _TOOL_DISPATCH_SLOTS.release()


def _spawn(
    command: Sequence[str], cwd: Path, env: Mapping[str, str]
) -> subprocess.Popen[bytes]:
    kwargs: dict[str, object] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "cwd": cwd,
        "env": dict(env),
        "close_fds": True,
    }
    if os.name == "nt":
        # Keep the initial thread suspended until it has been assigned to its
        # Job Object, so top-level Skill code cannot create an uncontained child.
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | _WINDOWS_CREATE_SUSPENDED
        )
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(list(command), **kwargs)


def _read_stdout(
    stream,
    output: queue.Queue[bytes | None | BaseException],
    stop: threading.Event,
) -> None:
    try:
        while not stop.is_set():
            line = stream.readline(_MAX_FRAME_BYTES + 1)
            if not line:
                _queue_reader_output(output, None, stop)
                return
            if len(line) > _MAX_FRAME_BYTES or not line.endswith(b"\n"):
                _queue_reader_output(
                    output, ValueError("frame too large or unterminated"), stop
                )
                return
            if not _queue_reader_output(output, line, stop):
                return
    except Exception as error:  # noqa: BLE001 - surface reader failure to supervisor
        _queue_reader_output(output, error, stop)


def _queue_reader_output(
    output: queue.Queue[bytes | None | BaseException],
    item: bytes | None | BaseException,
    stop: threading.Event,
) -> bool:
    while not stop.is_set():
        try:
            output.put(item, timeout=0.05)
            return True
        except queue.Full:
            continue
    return False


def _drain_stderr(stream) -> None:
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
    except (OSError, ValueError):
        return


def _terminate_process_tree(process: subprocess.Popen[bytes], job_handle: Any) -> None:
    if os.name == "nt":
        cleanup_error: Exception | None = None
        if job_handle is not None:
            try:
                _terminate_windows_job(job_handle)
            except Exception as error:  # noqa: BLE001 - job termination must fail closed
                cleanup_error = error
            try:
                _close_windows_handle(job_handle)
            except Exception as error:  # noqa: BLE001 - still close/kill what we can
                cleanup_error = cleanup_error or error
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
    else:
        try:
            # Kill the whole session in one operation. Sending TERM and then
            # targeting a potentially reaped group ID can race with PID reuse.
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)
    if os.name == "nt" and cleanup_error is not None:
        raise cleanup_error


def _terminate_windows_job(job_handle: Any) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    deadline = time.monotonic() + _WINDOWS_JOB_CLEANUP_TIMEOUT_SECONDS
    completion_signal_required = False

    while True:
        process_handles = _open_windows_job_process_handles(kernel32, job_handle)
        try:
            process_ids = _query_windows_job_process_ids(kernel32, job_handle)
            if (
                not process_ids
                and not process_handles
                and not completion_signal_required
            ):
                # An empty Job need not become signaled when members exit
                # naturally, so do not wait on the Job handle in this state.
                return

            if process_ids:
                if not kernel32.TerminateJobObject(job_handle, 1):
                    error = ctypes.get_last_error()
                    if _query_windows_job_process_ids(kernel32, job_handle):
                        raise ctypes.WinError(error)
                completion_signal_required = True

            for process_handle in process_handles:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "Windows Job members did not terminate before the cleanup deadline"
                    )
                timeout_ms = min(0xFFFFFFFE, max(1, int(remaining * 1000)))
                wait_result = _wait_windows_process_handle(
                    kernel32, process_handle, timeout_ms
                )
                if wait_result == 258:  # WAIT_TIMEOUT
                    raise TimeoutError(
                        "Windows Job member remained alive after termination"
                    )
                if wait_result != 0:  # WAIT_OBJECT_0
                    raise RuntimeError(
                        "Windows Job member returned an unexpected wait status"
                    )

            if completion_signal_required:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "Windows Job termination completion was not signaled before the cleanup deadline"
                    )
                timeout_ms = min(50, max(1, int(remaining * 1000)))
                wait_result = _wait_windows_process_handle(
                    kernel32, job_handle, timeout_ms
                )
                if wait_result == 0:  # WAIT_OBJECT_0: Job termination completed.
                    if not _query_windows_job_process_ids(kernel32, job_handle):
                        return
                elif wait_result == 258:  # WAIT_TIMEOUT: inspect for late members.
                    if deadline - time.monotonic() <= 0:
                        raise TimeoutError(
                            "Windows Job termination completion was not signaled before the cleanup deadline"
                        )
                else:
                    raise RuntimeError(
                        "Windows Job returned an unexpected completion wait status"
                    )
                continue

            # TerminateJobObject only acts on members present while the kernel
            # processes that request. A descendant may have joined after the
            # first PID snapshot, so re-enumerate until every captured process
            # is signaled and the Job has no active members.
            if not _query_windows_job_process_ids(kernel32, job_handle):
                return
        finally:
            _close_windows_process_handles(kernel32, process_handles)

        if deadline - time.monotonic() <= 0:
            raise TimeoutError(
                "Windows Job members kept appearing until the cleanup deadline"
            )


def _query_windows_job_active_process_count(kernel32: Any, job_handle: Any) -> int:
    import ctypes
    from ctypes import wintypes

    class BasicAccountingInformation(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    information = BasicAccountingInformation()
    returned = wintypes.DWORD()
    if not kernel32.QueryInformationJobObject(
        job_handle,
        1,  # JobObjectBasicAccountingInformation
        ctypes.byref(information),
        ctypes.sizeof(information),
        ctypes.byref(returned),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(information.ActiveProcesses)


def _query_windows_job_process_ids(kernel32: Any, job_handle: Any) -> list[int]:
    import ctypes
    from ctypes import wintypes

    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    capacity = 64
    while capacity <= 65536:
        class BasicProcessIdList(ctypes.Structure):
            _fields_ = [
                ("NumberOfAssignedProcesses", wintypes.DWORD),
                ("NumberOfProcessIdsInList", wintypes.DWORD),
                ("ProcessIdList", ctypes.c_size_t * capacity),
            ]

        information = BasicProcessIdList()
        returned = wintypes.DWORD()
        success = kernel32.QueryInformationJobObject(
            job_handle,
            3,  # JobObjectBasicProcessIdList
            ctypes.byref(information),
            ctypes.sizeof(information),
            ctypes.byref(returned),
        )
        error = 0 if success else ctypes.get_last_error()
        count = min(
            information.NumberOfProcessIdsInList,
            len(information.ProcessIdList),
        )
        process_ids = [int(pid) for pid in information.ProcessIdList[:count]]
        if success and information.NumberOfAssignedProcesses <= count:
            return process_ids
        if not success and error not in {234, 122}:  # ERROR_MORE_DATA / INSUFFICIENT_BUFFER
            raise ctypes.WinError(error)
        capacity = max(capacity * 2, information.NumberOfAssignedProcesses)
    raise RuntimeError("Windows Job process list exceeded the safe inspection limit")


def _open_windows_job_process_handles(kernel32: Any, job_handle: Any) -> list[Any]:
    import ctypes
    from ctypes import wintypes

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    kernel32.IsProcessInJob.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    process_handles: list[Any] = []
    inspected_process_ids: set[int] = set()
    try:
        for _ in range(32):
            for process_id in _query_windows_job_process_ids(kernel32, job_handle):
                if process_id in inspected_process_ids:
                    continue
                process_handle = kernel32.OpenProcess(
                    0x00101001,  # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE
                    False,
                    process_id,
                )
                if not process_handle:
                    error = ctypes.get_last_error()
                    if error == 87:  # ERROR_INVALID_PARAMETER: process exited before handle acquisition
                        inspected_process_ids.add(process_id)
                        continue
                    raise ctypes.WinError(error)
                process_handles.append(process_handle)
                inspected_process_ids.add(process_id)
                in_job = wintypes.BOOL()
                if not kernel32.IsProcessInJob(
                    process_handle, job_handle, ctypes.byref(in_job)
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not in_job.value:
                    _close_windows_process_handles(kernel32, [process_handle])
                    process_handles.pop()
            current_process_ids = set(
                _query_windows_job_process_ids(kernel32, job_handle)
            )
            if current_process_ids.issubset(inspected_process_ids):
                return process_handles
        raise RuntimeError("Windows Job process membership did not stabilize for cleanup")
    except Exception:
        _close_windows_process_handles(kernel32, process_handles)
        raise


def _close_windows_process_handles(kernel32: Any, handles: list[Any]) -> None:
    import ctypes

    first_error: OSError | None = None
    for handle in handles:
        if not kernel32.CloseHandle(handle) and first_error is None:
            first_error = ctypes.WinError(ctypes.get_last_error())
    if first_error is not None:
        raise first_error


def _wait_windows_process_handle(kernel32: Any, process_handle: Any, timeout: int) -> int:
    import ctypes
    from ctypes import wintypes

    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    result = int(kernel32.WaitForSingleObject(process_handle, timeout))
    if result == 0xFFFFFFFF:
        raise ctypes.WinError(ctypes.get_last_error())
    return result


def _is_crash(return_code: int) -> bool:
    if return_code < 0:
        return True
    return return_code in {3, 134, 139, 3221225477, 3221226505}


def _restrict_directory_permissions(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o700)


def _attach_windows_job(process: subprocess.Popen[bytes]) -> Any:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    class BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            (field, ctypes.c_ulonglong)
            for field in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimit),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        process.kill()
        raise SkillProcessError(
            "SKILL_PROCESS_ISOLATION_UNAVAILABLE",
            "Windows process-tree containment could not be initialized",
        )
    limits = ExtendedLimit()
    limits.BasicLimitInformation.LimitFlags = (
        0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    if not kernel32.SetInformationJobObject(
        handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
    ) or not kernel32.AssignProcessToJobObject(
        handle, wintypes.HANDLE(process._handle)
    ):
        kernel32.CloseHandle(handle)
        process.kill()
        raise SkillProcessError(
            "SKILL_PROCESS_ISOLATION_UNAVAILABLE",
            "Windows process-tree containment could not be enforced",
        )
    return handle


def _resume_windows_process(process_id: int) -> None:
    import ctypes
    from ctypes import wintypes

    class ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Thread32First.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ThreadEntry32),
    ]
    kernel32.Thread32Next.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ThreadEntry32),
    ]
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.OpenThread.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000004, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if not snapshot or snapshot == invalid_handle:
        raise SkillProcessError(
            "SKILL_PROCESS_ISOLATION_UNAVAILABLE",
            "Windows suspended process thread could not be located",
        )
    thread_handle = None
    try:
        entry = ThreadEntry32()
        entry.dwSize = ctypes.sizeof(ThreadEntry32)
        found = kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32OwnerProcessID == process_id:
                thread_handle = kernel32.OpenThread(0x0002, False, entry.th32ThreadID)
                break
            found = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        if not thread_handle:
            raise SkillProcessError(
                "SKILL_PROCESS_ISOLATION_UNAVAILABLE",
                "Windows suspended process thread could not be opened",
            )
        previous_suspend_count = kernel32.ResumeThread(thread_handle)
        if previous_suspend_count in {0, 0xFFFFFFFF}:
            raise SkillProcessError(
                "SKILL_PROCESS_ISOLATION_UNAVAILABLE",
                "Windows Skill process could not be safely resumed",
            )
    finally:
        if thread_handle:
            kernel32.CloseHandle(thread_handle)
        kernel32.CloseHandle(snapshot)


def _close_windows_handle(handle: Any) -> None:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if not kernel32.CloseHandle(handle):
        raise ctypes.WinError(ctypes.get_last_error())
