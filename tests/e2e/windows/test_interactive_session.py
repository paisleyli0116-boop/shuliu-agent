from __future__ import annotations

import ctypes
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import pytest
import win32gui
import win32process

from interactive_fixture_diagnostics import (
    parse_fixture_stage_markers,
    sanitize_fixture_diagnostics,
    terminate_process_bounded,
)


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Interactive User Session only")


def _process_session_id(process_id: int) -> int | None:
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ProcessIdToSessionId.argtypes = [ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.ProcessIdToSessionId.restype = ctypes.c_int
        session_id = ctypes.c_ulong()
        if not kernel32.ProcessIdToSessionId(process_id, ctypes.byref(session_id)):
            return None
        return int(session_id.value)
    except Exception:
        return None


def _matching_fixture_windows(title: str) -> tuple[list[tuple[int, int, bool]], str]:
    matches: list[tuple[int, int, bool]] = []
    scan_status = "OK"

    def find_window(hwnd, _extra):
        nonlocal scan_status
        try:
            if not win32gui.IsWindow(hwnd) or win32gui.GetWindowText(hwnd) != title:
                return True
            _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
            matches.append((int(process_id), int(hwnd), bool(win32gui.IsWindowVisible(hwnd))))
        except Exception:
            scan_status = "FAILED"
        return True

    try:
        win32gui.EnumWindows(find_window, None)
    except Exception:
        scan_status = "FAILED"
    return matches, scan_status


def _read_fixture_stages(path: Path) -> tuple[list[dict[str, object]], str]:
    try:
        contents = path.read_text(encoding="ascii")
    except (OSError, UnicodeError):
        return [], "UNAVAILABLE"
    return parse_fixture_stage_markers(contents), "OK"


def test_interactive_session_executes_and_verifies_a_real_uia_action():
    from shuliu_runtime.approval.service import ApprovalError
    from shuliu_runtime.computer.adapter import ComputerRuntime
    from shuliu_runtime.policy.engine import PolicyEngine
    from shuliu_runtime.policy.risk import RiskLevel
    from shuliu_runtime.policy.rules import PolicyOutcome, PolicyRule
    from shuliu_runtime.tools.broker import IdentityPrincipal
    from shuliu_runtime.tools.idempotency import InMemoryIdempotencyStore
    from pywinauto import Desktop

    class Identity:
        def resolve(self, actor):
            if actor.get("subject_id") != "task25-e2e":
                return None
            return IdentityPrincipal("task25-e2e", "org-task25", ("computer.read", "computer.act"))

    class Approval:
        def consume(self, approval_id, action_context):
            if approval_id != "task25-approved":
                raise ApprovalError("APPROVAL_NOT_FOUND", "approval is unavailable")

    evidence = []

    def save_evidence(record):
        evidence.append(record)
        return f"task25-evidence-{len(evidence)}"

    rules = [
        PolicyRule("computer-read", "computer.capability", RiskLevel.LOW,
            decision=PolicyOutcome.ALLOW, organization_id="org-task25", permission="computer.read"),
        PolicyRule("computer-observe", "computer.observe", RiskLevel.LOW,
            decision=PolicyOutcome.ALLOW, organization_id="org-task25", permission="computer.read"),
        PolicyRule("computer-act", "computer.act", RiskLevel.CRITICAL,
            decision=PolicyOutcome.ALLOW, organization_id="org-task25", permission="computer.act"),
    ]
    runtime = ComputerRuntime(
        identity=Identity(),
        policy=PolicyEngine(rules, policy_version="task25-windows-e2e-v1"),
        approvals=Approval(),
        idempotency_store=InMemoryIdempotencyStore(),
        event_sink=lambda _event: None,
        evidence_sink=save_evidence,
    )
    actor = {"subject_id": "task25-e2e"}
    context = {
        "organization_id": "org-task25",
        "trace_id": "trace-task25-windows-e2e",
        "privacy": "INTERNAL",
        "approval_id": "task25-approved",
        "target_approver_id": "task25-local-reviewer",
        "action_summary": "Type a non-sensitive marker into the task-owned WinForms window",
    }
    process = None
    diagnostic_directory = None
    diagnostic_input = None
    startup_failure = None
    cleanup_state = "NOT_STARTED"
    diagnostic_temp_cleanup_state = "NOT_CREATED"
    try:
        capability = runtime.capability(actor=actor, context=context)
        if capability.output["status"] != "AVAILABLE":
            unavailable_diagnostics = sanitize_fixture_diagnostics(
                {
                    "launch_succeeded": False,
                    "process_state": "NOT_STARTED",
                    "parent_session_status": capability.output.get("status"),
                    "parent_session_reason_code": capability.output.get("reason_code"),
                    "parent_session_id": capability.output.get("session_id"),
                    "parent_desktop_name": capability.output.get("desktop_name"),
                    "window_scan_status": "UNKNOWN",
                    "stage_file_status": "UNAVAILABLE",
                    "cleanup_state": "NOT_STARTED",
                    "diagnostic_temp_cleanup_state": "NOT_CREATED",
                }
            )
            reason = (
                "COMPUTER_RUNTIME_NOT_EXECUTABLE: interactive session capability is unavailable; "
                f"fixture_diagnostics={json.dumps(unavailable_diagnostics, sort_keys=True, separators=(',', ':'))}"
            )
            if os.environ.get("SHULIU_REQUIRE_INTERACTIVE_COMPUTER") == "1":
                pytest.fail(reason)
            pytest.skip(reason)

        title = f"ShuLiu Task 25 Fixture {uuid4()}"
        diagnostic_directory = tempfile.TemporaryDirectory(prefix="shuliu-winforms-")
        stage_path = Path(diagnostic_directory.name) / "fixture-stages.log"
        escaped_stage_path = str(stage_path).replace("'", "''")
        script = "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                f"$diagnosticPath = '{escaped_stage_path}'",
                "$watch = [System.Diagnostics.Stopwatch]::StartNew()",
                "$writeStage = { param([string]$stage) $line = $stage + '|' + $watch.ElapsedMilliseconds + [Environment]::NewLine; [System.IO.File]::AppendAllText($diagnosticPath, $line, [System.Text.Encoding]::ASCII) }.GetNewClosure()",
                "try {",
                "  & $writeStage 'PROCESS_STARTED'",
                "  Add-Type -AssemblyName System.Windows.Forms",
                "  & $writeStage 'WINFORMS_ASSEMBLY_LOADED'",
                "  $form = [System.Windows.Forms.Form]::new()",
                f"  $form.Text = '{title}'",
                "  $form.Width = 720",
                "  $form.Height = 420",
                "  $edit = [System.Windows.Forms.TextBox]::new()",
                "  $edit.Name = 'Task25Edit'",
                "  $edit.AccessibleName = 'Document'",
                "  $edit.Multiline = $true",
                "  $edit.Left = 12",
                "  $edit.Top = 12",
                "  $edit.Width = 680",
                "  $edit.Height = 340",
                "  $form.Controls.Add($edit)",
                "  & $writeStage 'FORM_CREATED'",
                "  $handleCreated = { & $writeStage 'FORM_HANDLE_CREATED' }.GetNewClosure()",
                "  $form.add_HandleCreated($handleCreated)",
                "  & $writeStage 'FORM_CONFIGURED'",
                "  & $writeStage 'APPLICATION_RUN_ENTERED'",
                "  [System.Windows.Forms.Application]::Run($form)",
                "  & $writeStage 'APPLICATION_RUN_RETURNED'",
                "} catch {",
                "  $errorType = $_.Exception.GetType().Name",
                "  if ($errorType -notin @('ArgumentException','ExternalException','FileLoadException','FileNotFoundException','InvalidOperationException','PlatformNotSupportedException','TypeLoadException','UnauthorizedAccessException','Win32Exception')) { $errorType = 'OTHER' }",
                "  & $writeStage ('FIXTURE_ERROR:' + $errorType)",
                "  exit 70",
                "}",
            ]
        )
        launch_error_type = None
        try:
            process = subprocess.Popen(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-STA", "-Command", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            launch_error_type = type(exc).__name__

        window_handle = None
        window_count = 0
        owned_window_count = 0
        visible_window_count = 0
        window_scan_status = "OK"
        fixture_started_at = time.monotonic()
        if process is None:
            startup_failure = "Task-owned WinForms fixture process could not be started"
        else:
            deadline = fixture_started_at + 15
            while time.monotonic() < deadline:
                exit_code = process.poll()
                matches, scan_status = _matching_fixture_windows(title)
                window_scan_status = "FAILED" if scan_status == "FAILED" else window_scan_status
                window_count = len(matches)
                owned = [match for match in matches if match[0] == process.pid]
                owned_window_count = len(owned)
                visible = [match for match in owned if match[2]]
                visible_window_count = len(visible)
                if exit_code is not None:
                    startup_failure = "Task-owned WinForms fixture exited before showing a window"
                    break
                if visible:
                    window_handle = visible[0][1]
                    break
                time.sleep(0.2)
            if window_handle is None and startup_failure is None:
                startup_failure = "Task-owned WinForms fixture window did not become visible"

        stages, stage_file_status = _read_fixture_stages(stage_path)
        process_exit_code = process.poll() if process is not None else None
        parent_session_id = capability.output.get("session_id")
        diagnostic_input = {
            "launch_succeeded": process is not None,
            "launch_error_type": launch_error_type,
            "process_state": (
                "NOT_STARTED" if process is None else "EXITED" if process_exit_code is not None else "RUNNING"
            ),
            "exit_code": process_exit_code,
            "stages": stages,
            "stage_file_status": stage_file_status,
            "parent_session_status": capability.output.get("status"),
            "parent_session_reason_code": capability.output.get("reason_code"),
            "parent_session_id": parent_session_id,
            "parent_desktop_name": capability.output.get("desktop_name"),
            "child_session_id": _process_session_id(process.pid) if process is not None else None,
            "window_scan_status": window_scan_status,
            "window_count": window_count,
            "owned_window_count": owned_window_count,
            "visible_window_count": visible_window_count,
            "elapsed_ms": int((time.monotonic() - fixture_started_at) * 1000),
            "cleanup_state": "NOT_STARTED",
        }

        if startup_failure is None:
            assert window_handle is not None
            stage_names = {stage["stage"] for stage in stages}
            assert stage_file_status == "OK"
            assert {
                "PROCESS_STARTED",
                "WINFORMS_ASSEMBLY_LOADED",
                "FORM_CREATED",
                "FORM_HANDLE_CREATED",
            } <= stage_names
            assert diagnostic_input["process_state"] == "RUNNING"
            assert diagnostic_input["parent_session_id"] == diagnostic_input["child_session_id"]
            assert diagnostic_input["owned_window_count"] >= 1
            assert diagnostic_input["visible_window_count"] >= 1
            window_ref = f"0x{window_handle:x}"
            observed = runtime.observe(window_ref, actor=actor, context=context)
            assert observed.output.get("status") == "OBSERVED", observed.output
            assert observed.output["trust"] == "UNTRUSTED_UI_CONTENT"
            edit = next(
                item for item in observed.output["elements"]
                if item["role"] == "Edit" and not item["sensitive"]
            )
            marker = "ShuLiu Task 25 UIA smoke"
            result = runtime.act(
                {
                    "kind": "type",
                    "window_ref": window_ref,
                    "observation_id": observed.output["observation_id"],
                    "element_ref": edit["element_ref"],
                    "text": marker,
                },
                actor=actor,
                context=context,
                idempotency_key="task25-notepad-type-1",
            )

            assert result.output["status"] == "VERIFIED"
            assert result.verification_status.value == "VERIFIED"
            window = Desktop(backend="uia").window(handle=window_handle).wrapper_object()
            editor = next(control for control in window.descendants(control_type="Edit"))
            assert marker in editor.get_value()
            assert evidence and all(item["session"]["state"] == "AVAILABLE" for item in evidence)
            assert all(item["machine"] and item["session"]["session_id"] is not None for item in evidence)
    finally:
        cleanup_state = terminate_process_bounded(process) if process is not None else "NOT_STARTED"
        if diagnostic_directory is not None:
            try:
                diagnostic_directory.cleanup()
                diagnostic_temp_cleanup_state = "CLEANED"
            except Exception:
                diagnostic_temp_cleanup_state = "FAILED"

    if diagnostic_input is not None:
        diagnostic_input["cleanup_state"] = cleanup_state
        diagnostic_input["diagnostic_temp_cleanup_state"] = diagnostic_temp_cleanup_state
    safe_diagnostics = sanitize_fixture_diagnostics(diagnostic_input or {})
    if startup_failure is not None:
        pytest.fail(
            f"{startup_failure}; fixture_diagnostics="
            f"{json.dumps(safe_diagnostics, sort_keys=True, separators=(',', ':'))}"
        )
    if cleanup_state in {"STILL_RUNNING", "UNKNOWN"} or diagnostic_temp_cleanup_state == "FAILED":
        pytest.fail(
            "Task-owned WinForms fixture or diagnostic cleanup did not complete; fixture_diagnostics="
            f"{json.dumps(safe_diagnostics, sort_keys=True, separators=(',', ':'))}"
        )
