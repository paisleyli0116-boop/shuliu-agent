from __future__ import annotations

import math
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any

_SAFE_STAGES = frozenset(
    {
        "PROCESS_STARTED",
        "WINFORMS_ASSEMBLY_LOADED",
        "FORM_CREATED",
        "FORM_CONFIGURED",
        "APPLICATION_RUN_ENTERED",
        "APPLICATION_RUN_RETURNED",
        "FORM_HANDLE_CREATED",
        "FORM_CLOSED",
        "FIXTURE_ERROR",
    }
)
_SAFE_ERROR_TYPES = frozenset(
    {
        "ArgumentException",
        "ExternalException",
        "FileLoadException",
        "FileNotFoundException",
        "InvalidOperationException",
        "PlatformNotSupportedException",
        "TypeLoadException",
        "UnauthorizedAccessException",
        "Win32Exception",
    }
)
_SAFE_SESSION_REASONS = frozenset(
    {
        "COMPUTER_INPUT_DESKTOP_UNAVAILABLE",
        "COMPUTER_SESSION_LOCKED",
        "COMPUTER_SESSION_NOT_CONNECTED",
        "COMPUTER_SESSION_NOT_INTERACTIVE",
        "COMPUTER_SESSION_STATE_UNKNOWN",
        "COMPUTER_WINDOWS_ONLY",
    }
)
_PROCESS_STATES = frozenset({"NOT_STARTED", "RUNNING", "EXITED", "UNKNOWN"})
_CLEANUP_STATES = frozenset(
    {
        "NOT_STARTED",
        "ALREADY_EXITED",
        "EXITED_AFTER_KILL_REQUEST",
        "EXITED_AFTER_TERMINATE_REQUEST",
        "STILL_RUNNING",
        "UNKNOWN",
    }
)
_LAUNCH_ERROR_TYPES = frozenset({"FileNotFoundError", "OSError", "PermissionError"})
_TEMP_CLEANUP_STATES = frozenset({"NOT_CREATED", "CLEANED", "FAILED"})


def parse_fixture_stage_markers(contents: str | None) -> list[dict[str, Any]]:
    """Parse a bounded, allowlisted stage file written by the owned fixture."""
    if not isinstance(contents, str):
        return []

    stages: list[dict[str, Any]] = []
    for line in contents[:8192].splitlines()[:64]:
        name, separator, elapsed = line.partition("|")
        if not separator or not elapsed.isascii() or not elapsed.isdecimal():
            continue
        elapsed_ms = int(elapsed)
        if elapsed_ms > 600_000:
            continue

        error_type: str | None = None
        if name.startswith("FIXTURE_ERROR:"):
            candidate = name.partition(":")[2]
            name = "FIXTURE_ERROR"
            error_type = candidate if candidate in _SAFE_ERROR_TYPES else "OTHER"
        if name not in _SAFE_STAGES:
            continue

        record: dict[str, Any] = {"stage": name, "elapsed_ms": elapsed_ms}
        if error_type is not None:
            record["error_type"] = error_type
        stages.append(record)
    return stages


def sanitize_fixture_diagnostics(values: Mapping[str, object]) -> dict[str, Any]:
    """Return only fixed, non-sensitive fields suitable for CI assertion output."""
    stages = _sanitize_stages(values.get("stages"))
    stage_names = {stage["stage"] for stage in stages}
    parent_session_id = _safe_int(values.get("parent_session_id"), maximum=0xFFFFFFFF)
    child_session_id = _safe_int(values.get("child_session_id"), maximum=0xFFFFFFFF)

    parent_status = values.get("parent_session_status")
    if not isinstance(parent_status, str) or parent_status not in {"AVAILABLE", "UNAVAILABLE"}:
        parent_status = "UNKNOWN"
    reason_code = values.get("parent_session_reason_code")
    if not isinstance(reason_code, str) or reason_code not in _SAFE_SESSION_REASONS:
        reason_code = None
    desktop = values.get("parent_desktop_name")
    if not isinstance(desktop, str):
        desktop = None
    elif desktop.casefold() == "default":
        desktop = "Default"
    elif desktop.casefold() == "winlogon":
        desktop = "Winlogon"
    else:
        desktop = "Other"

    process_state = values.get("process_state")
    if not isinstance(process_state, str) or process_state not in _PROCESS_STATES:
        process_state = "UNKNOWN"
    cleanup_state = values.get("cleanup_state")
    if not isinstance(cleanup_state, str) or cleanup_state not in _CLEANUP_STATES:
        cleanup_state = "UNKNOWN"
    window_scan_status = values.get("window_scan_status")
    if not isinstance(window_scan_status, str) or window_scan_status not in {"OK", "FAILED"}:
        window_scan_status = "UNKNOWN"
    stage_file_status = values.get("stage_file_status")
    if not isinstance(stage_file_status, str) or stage_file_status not in {"OK", "UNAVAILABLE"}:
        stage_file_status = "UNKNOWN"
    temp_cleanup_state = values.get("diagnostic_temp_cleanup_state")
    if not isinstance(temp_cleanup_state, str) or temp_cleanup_state not in _TEMP_CLEANUP_STATES:
        temp_cleanup_state = "UNKNOWN"
    launch_error_type = values.get("launch_error_type")
    if not isinstance(launch_error_type, str) or launch_error_type not in _LAUNCH_ERROR_TYPES:
        launch_error_type = "OTHER" if launch_error_type is not None else None

    same_session = None
    if parent_session_id is not None and child_session_id is not None:
        same_session = parent_session_id == child_session_id

    return {
        "fixture_process_created": values.get("launch_succeeded") is True,
        "fixture_script_started": "PROCESS_STARTED" in stage_names,
        "fixture_launch_error_type": launch_error_type,
        "fixture_process_state": process_state,
        "fixture_exit_code": _safe_int(values.get("exit_code"), maximum=0xFFFFFFFF),
        "fixture_stage_file_status": stage_file_status,
        "fixture_stages": stages,
        "fixture_error_type": next(
            (stage["error_type"] for stage in reversed(stages) if "error_type" in stage),
            None,
        ),
        "parent_session_status": parent_status,
        "parent_session_reason_code": reason_code,
        "parent_session_id": parent_session_id,
        "parent_desktop_name": desktop,
        "child_session_id": child_session_id,
        "same_session": same_session,
        "window_scan_status": window_scan_status,
        "matching_window_count": _safe_int(values.get("window_count"), maximum=1000) or 0,
        "task_owned_window_count": _safe_int(values.get("owned_window_count"), maximum=1000) or 0,
        "task_owned_visible_window_count": _safe_int(values.get("visible_window_count"), maximum=1000) or 0,
        "elapsed_ms": _safe_int(values.get("elapsed_ms"), maximum=600_000) or 0,
        "cleanup_state": cleanup_state,
        "diagnostic_temp_cleanup_state": temp_cleanup_state,
    }


def terminate_process_bounded(
    process: Any,
    *,
    terminate_timeout: float = 5.0,
    kill_timeout: float = 5.0,
) -> str:
    """Terminate an owned fixture and always wait again after a kill request."""
    if not _safe_timeout(terminate_timeout) or not _safe_timeout(kill_timeout):
        return "UNKNOWN"
    try:
        if process.poll() is not None:
            return "ALREADY_EXITED"
    except Exception:
        return "UNKNOWN"

    try:
        process.terminate()
    except Exception:
        pass
    try:
        process.wait(timeout=terminate_timeout)
        return "EXITED_AFTER_TERMINATE_REQUEST"
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass

    try:
        process.kill()
    except Exception:
        pass
    try:
        process.wait(timeout=kill_timeout)
        return "EXITED_AFTER_KILL_REQUEST"
    except subprocess.TimeoutExpired:
        return "STILL_RUNNING"
    except Exception:
        return "UNKNOWN"


def _sanitize_stages(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    result: list[dict[str, Any]] = []
    for item in raw[:64]:
        if not isinstance(item, Mapping):
            continue
        stage = item.get("stage")
        elapsed_ms = _safe_int(item.get("elapsed_ms"), maximum=600_000)
        if not isinstance(stage, str) or stage not in _SAFE_STAGES or elapsed_ms is None:
            continue
        record: dict[str, Any] = {"stage": stage, "elapsed_ms": elapsed_ms}
        error_type = item.get("error_type")
        if stage == "FIXTURE_ERROR":
            record["error_type"] = (
                error_type if isinstance(error_type, str) and error_type in _SAFE_ERROR_TYPES else "OTHER"
            )
        result.append(record)
    return result


def _safe_int(value: object, *, maximum: int) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > maximum:
        return None
    return value


def _safe_timeout(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= 10
    )
