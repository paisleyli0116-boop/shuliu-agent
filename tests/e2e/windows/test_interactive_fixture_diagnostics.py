import json
import subprocess

from interactive_fixture_diagnostics import (
    parse_fixture_stage_markers,
    sanitize_fixture_diagnostics,
    terminate_process_bounded,
)


def test_stage_markers_keep_only_known_stages_and_bounded_timing():
    parsed = parse_fixture_stage_markers(
        "PROCESS_STARTED|0\nWINFORMS_ASSEMBLY_LOADED|31\nFORM_CREATED|48\n"
        "FIXTURE_ERROR:InvalidOperationException|59\nAPI_KEY=never-print-this|60\n"
    )

    assert parsed == [
        {"stage": "PROCESS_STARTED", "elapsed_ms": 0},
        {"stage": "WINFORMS_ASSEMBLY_LOADED", "elapsed_ms": 31},
        {"stage": "FORM_CREATED", "elapsed_ms": 48},
        {"stage": "FIXTURE_ERROR", "error_type": "InvalidOperationException", "elapsed_ms": 59},
    ]


def test_fixture_diagnostics_are_allowlisted_and_never_echo_raw_errors():
    report = sanitize_fixture_diagnostics(
        {
            "launch_succeeded": True,
            "launch_error_type": None,
            "process_state": "RUNNING",
            "exit_code": None,
            "stages": [{"stage": "FORM_CREATED", "elapsed_ms": 48}, {"stage": "UNSAFE", "elapsed_ms": 49}],
            "stage_file_status": "OK",
            "parent_session_status": "AVAILABLE",
            "parent_session_reason_code": None,
            "parent_session_id": 1,
            "parent_desktop_name": "Default",
            "child_session_id": 1,
            "window_scan_status": "OK",
            "window_count": 1,
            "owned_window_count": 1,
            "visible_window_count": 0,
            "elapsed_ms": 15000,
            "cleanup_state": "EXITED_AFTER_KILL_REQUEST",
            "diagnostic_temp_cleanup_state": "CLEANED",
            "raw_exception": "sk-live-never-print-this",
            "stderr": "Authorization: Bearer never-print-this",
            "title": "private-window-title",
        }
    )
    serialized = json.dumps(report, sort_keys=True)

    assert "never-print-this" not in serialized
    assert "private-window-title" not in serialized
    assert report["fixture_process_state"] == "RUNNING"
    assert report["fixture_process_created"] is True
    assert report["fixture_script_started"] is False
    assert report["fixture_stage_file_status"] == "OK"
    assert report["matching_window_count"] == 1
    assert report["task_owned_visible_window_count"] == 0
    assert report["fixture_stages"] == [{"stage": "FORM_CREATED", "elapsed_ms": 48}]
    assert report["diagnostic_temp_cleanup_state"] == "CLEANED"


def test_fixture_diagnostics_reject_malformed_field_types_without_crashing():
    report = sanitize_fixture_diagnostics(
        {
            "launch_error_type": [],
            "process_state": [],
            "stages": [{"stage": [], "elapsed_ms": 1, "error_type": []}],
            "parent_session_status": [],
            "parent_session_reason_code": {},
            "cleanup_state": [],
            "diagnostic_temp_cleanup_state": [],
            "window_scan_status": {},
            "stage_file_status": [],
        }
    )

    assert report["fixture_process_state"] == "UNKNOWN"
    assert report["fixture_stages"] == []
    assert report["parent_session_status"] == "UNKNOWN"
    assert report["parent_session_reason_code"] is None
    assert report["cleanup_state"] == "UNKNOWN"
    assert report["diagnostic_temp_cleanup_state"] == "UNKNOWN"
    assert report["window_scan_status"] == "UNKNOWN"
    assert report["fixture_stage_file_status"] == "UNKNOWN"
    assert report["fixture_launch_error_type"] == "OTHER"


class _FakeProcess:
    def __init__(self, *, terminate_exits=False, kill_exits=False):
        self.returncode = None
        self.terminate_exits = terminate_exits
        self.kill_exits = kill_exits
        self.terminated = False
        self.killed = False
        self.wait_timeouts = []

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if self.terminate_exits:
            self.returncode = 1

    def kill(self):
        self.killed = True
        if self.kill_exits:
            self.returncode = -9

    def wait(self, timeout):
        self.wait_timeouts.append(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fixture", timeout)
        return self.returncode


def test_fixture_cleanup_waits_after_terminate_and_kill_with_bounds():
    process = _FakeProcess(kill_exits=True)

    result = terminate_process_bounded(process, terminate_timeout=0.25, kill_timeout=0.5)

    assert result == "EXITED_AFTER_KILL_REQUEST"
    assert process.terminated is True
    assert process.killed is True
    assert process.wait_timeouts == [0.25, 0.5]


def test_fixture_cleanup_reports_still_running_instead_of_claiming_success():
    process = _FakeProcess()

    result = terminate_process_bounded(process, terminate_timeout=0.25, kill_timeout=0.5)

    assert result == "STILL_RUNNING"
    assert process.wait_timeouts == [0.25, 0.5]


def test_fixture_cleanup_does_not_signal_an_already_exited_process():
    process = _FakeProcess()
    process.returncode = 0

    result = terminate_process_bounded(process)

    assert result == "ALREADY_EXITED"
    assert process.terminated is False
    assert process.killed is False
