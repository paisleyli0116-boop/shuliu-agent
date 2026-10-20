"""Allowlisted diagnostics for private Browser Runtime failure analysis."""
from __future__ import annotations

import re


STAGES = frozenset({
    "import_browser", "construct_browser", "start_browser", "create_page",
    "register_guards", "configure_targets", "configure_network_guard",
    "disable_downloads", "read_browser_version", "navigate_goto",
    "navigate_wait_ready", "observe_dom_settle", "observe_snapshot", "request",
})
FAILURE_KINDS = frozenset({
    "CHROME_SANDBOX_UNAVAILABLE", "CHROME_PROCESS_EXITED",
    "CHROME_EXECUTABLE_UNAVAILABLE", "PERMISSION_DENIED",
    "CONNECTION_REFUSED", "NAVIGATION_FAILED", "OPERATION_TIMEOUT",
    "UPSTREAM_ERROR",
})


def failure_diagnostic(stage: object, error: BaseException) -> dict[str, str]:
    """Return only fixed stage, exception type and failure category fields.

    The exception message is inspected in memory to select a fixed category; it
    is never included in a protocol response or log.
    """
    safe_stage = stage if isinstance(stage, str) and stage in STAGES else "unknown"
    exception_type = type(error).__name__
    if not isinstance(exception_type, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", exception_type):
        exception_type = "Exception"

    message = str(error).casefold()
    if any(marker in message for marker in ("no usable sandbox", "failed to move to new namespace", "suid sandbox", "zygote host")):
        kind = "CHROME_SANDBOX_UNAVAILABLE"
    elif "executable doesn't exist" in message or "executable does not exist" in message or "no such file or directory" in message:
        kind = "CHROME_EXECUTABLE_UNAVAILABLE"
    elif "permission denied" in message or "operation not permitted" in message:
        kind = "PERMISSION_DENIED"
    elif "connection refused" in message or "econnrefused" in message:
        kind = "CONNECTION_REFUSED"
    elif "navigation failed:" in message:
        kind = "NAVIGATION_FAILED"
    elif isinstance(error, TimeoutError) or "timed out" in message or "timeout" in message:
        kind = "OPERATION_TIMEOUT"
    elif "failed to launch browser" in message or "browser process exited" in message or "browser closed" in message:
        kind = "CHROME_PROCESS_EXITED"
    else:
        kind = "UPSTREAM_ERROR"
    return {"stage": safe_stage, "exception_type": exception_type, "failure_kind": kind}


def validate_driver_diagnostic(value: object) -> dict[str, str] | None:
    """Accept only the diagnostic shape emitted by the trusted driver."""
    if not isinstance(value, dict) or set(value) != {"stage", "exception_type", "failure_kind"}:
        return None
    stage, exception_type, failure_kind = (value.get("stage"), value.get("exception_type"), value.get("failure_kind"))
    if not isinstance(stage, str) or (stage not in STAGES and stage != "unknown"):
        return None
    if not isinstance(exception_type, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", exception_type):
        return None
    if not isinstance(failure_kind, str) or failure_kind not in FAILURE_KINDS:
        return None
    return {"stage": stage, "exception_type": exception_type, "failure_kind": failure_kind}


def validate_progress(value: object) -> str | None:
    """Return a known stage from a minimal progress frame, or reject it."""
    if not isinstance(value, dict) or set(value) != {"stage"}:
        return None
    stage = value.get("stage")
    return stage if isinstance(stage, str) and stage in STAGES else None
