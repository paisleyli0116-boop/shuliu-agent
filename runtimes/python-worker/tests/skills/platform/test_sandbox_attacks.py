"""Windows runner attack validation for the Skill sandbox (Task 36).

These tests exercise the *real* production containment mechanism —
`process_host._spawn` (suspended creation), `_attach_windows_job`,
`_resume_windows_process` and Job kill-on-close — and record which
REQUIRED_SANDBOX_CONTROLS it can actually prove on Windows.

Outcome encoded by this evidence:

- `separate_process` is provable (Job containment, breakaway denial,
  descendant kill-on-close, suspended-spawn-before-binding).
- `filesystem_scope`, `network_scope`, `secret_isolation`,
  `foundation_db_isolation` and `no_direct_os_control` are NOT enforced
  by a Job Object: the attack children below *succeed* at reading
  unauthorized files, opening sockets, running OS commands and reading
  same-user secret/database files.  Therefore the Windows provider must
  report only the provable control and keep executable Skills disabled
  (fail closed) on this platform.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from shuliu_runtime.skills import process_host
from shuliu_runtime.skills.platform.windows import WindowsJobSandboxProvider
from shuliu_runtime.skills.process_host import SkillProcessHost
from shuliu_runtime.skills.sandbox import (
    REQUIRED_SANDBOX_CONTROLS,
    SandboxPolicy,
    SandboxUnavailableError,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows runner only")

_PROVABLE_CONTROLS = frozenset({"separate_process"})
_UNPROVEN_CONTROLS = REQUIRED_SANDBOX_CONTROLS - _PROVABLE_CONTROLS


def _run_job_contained_child(
    code: str, tmp_path: Path, *, timeout: float = 30, extra_env: dict[str, str] | None = None
) -> tuple[int, dict[str, Any], Any]:
    """Run a child process with the exact production containment mechanism."""
    script = tmp_path / "attack-main.py"
    script.write_text(code, encoding="utf-8")
    report_path = tmp_path / "attack-report.json"
    child_env = process_host._minimal_environment(tmp_path)
    if extra_env:
        child_env = {**child_env, **extra_env}
    # Production mechanism: CREATE_SUSPENDED spawn, Job assignment before resume.
    process = process_host._spawn([sys.executable, "-I", str(script)], tmp_path, child_env)
    job_handle = process_host._attach_windows_job(process)
    try:
        process_host._resume_windows_process(process.pid)
        out, err = process.communicate(timeout=timeout)
        report: dict[str, Any] = {}
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        return process.returncode, {"report": report, "stderr": err.decode("utf-8", "replace")}, job_handle
    finally:
        process_host._close_windows_handle(job_handle)


def test_windows_provider_reports_only_provable_controls_and_blocks_activation(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "skill-ran"
    package = tmp_path / "package"
    package.mkdir()
    (package / "main.py").write_text(
        f"import pathlib\npathlib.Path({str(marker)!r}).write_text('ran')\n",
        encoding="utf-8",
    )
    provider = WindowsJobSandboxProvider()

    assessment = provider.assess(SandboxPolicy(skill_id="attack-check", permissions=()))

    assert assessment.supported is False
    assert assessment.enforced_controls == _PROVABLE_CONTROLS
    for control in _UNPROVEN_CONTROLS:
        assert control in assessment.reason
    with pytest.raises(SandboxUnavailableError):
        provider.wrap_command(["python", "-c", "pass"], SandboxPolicy(skill_id="x", permissions=()), tmp_path)

    host = SkillProcessHost(sandbox_provider=provider)
    with pytest.raises(SandboxUnavailableError) as denied:
        host.run(
            package,
            SandboxPolicy(skill_id="attack-check", permissions=()),
            {},
            timeout=1,
        )
    assert "filesystem_scope" in str(denied.value)
    assert "network_scope" in str(denied.value)
    assert not marker.exists(), "executable Skill must not start while controls are unproven"


def test_windows_job_containment_does_not_restrict_file_reads(tmp_path: Path) -> None:
    secret_file = tmp_path / "unauthorized.txt"
    secret_file.write_text("outside-package-value", encoding="utf-8")
    report = tmp_path / "attack-report.json"
    code = (
        "import json, pathlib, sys\n"
        f"value = pathlib.Path({str(secret_file)!r}).read_text(encoding='utf-8')\n"
        f"pathlib.Path({str(report)!r}).write_text(json.dumps({{'read_succeeded': value == 'outside-package-value'}}))\n"
    )
    return_code, result, _job = _run_job_contained_child(code, tmp_path)

    assert return_code == 0, result["stderr"]
    assert result["report"] == {"read_succeeded": True}, (
        "unexpected: Job Object restricted file access; re-assess filesystem_scope"
    )


def test_windows_job_containment_does_not_restrict_network_access(tmp_path: Path) -> None:
    # The production minimal environment omits SystemRoot, which breaks
    # Winsock initialization (WSASYSNOTREADY) *independently* of any Job
    # restriction.  Restore the system roots so this attack measures the
    # Job Object itself, not environment breakage.
    extra_env = {
        key: os.environ[key]
        for key in ("SystemRoot", "SystemDrive", "windir")
        if key in os.environ
    }
    report = tmp_path / "attack-report.json"
    code = (
        "import json, pathlib, socket\n"
        "listener = socket.socket()\n"
        "listener.bind(('127.0.0.1', 0))\n"
        "listener.listen(1)\n"
        "client = socket.create_connection(listener.getsockname(), timeout=2)\n"
        "conn, _ = listener.accept()\n"
        "client.sendall(b'ping')\n"
        "payload = conn.recv(4)\n"
        "listener.close(); client.close(); conn.close()\n"
        f"pathlib.Path({str(report)!r}).write_text(json.dumps({{'network_succeeded': payload == b'ping'}}))\n"
    )
    return_code, result, _job = _run_job_contained_child(code, tmp_path, extra_env=extra_env)

    assert return_code == 0, result["stderr"]
    assert result["report"] == {"network_succeeded": True}, (
        "unexpected: Job Object restricted network access; re-assess network_scope"
    )


def test_windows_job_containment_does_not_restrict_os_command_execution(tmp_path: Path) -> None:
    report = tmp_path / "attack-report.json"
    code = (
        "import json, pathlib, subprocess, sys\n"
        "completed = subprocess.run(\n"
        "    [sys.executable, '-c', \"print('os-command-ok')\"],\n"
        "    capture_output=True,\n"
        "    text=True,\n"
        "    timeout=10,\n"
        ")\n"
        f"pathlib.Path({str(report)!r}).write_text(json.dumps({{'os_command_succeeded': completed.stdout.strip() == 'os-command-ok'}}))\n"
    )
    return_code, result, _job = _run_job_contained_child(code, tmp_path)

    assert return_code == 0, result["stderr"]
    assert result["report"] == {"os_command_succeeded": True}, (
        "unexpected: Job Object restricted OS command execution; re-assess no_direct_os_control"
    )


def test_windows_job_containment_does_not_isolate_same_user_secret_files(tmp_path: Path) -> None:
    secret_file = tmp_path / "foundation-secret.txt"
    secret_file.write_text("s3cret-value", encoding="utf-8")
    report = tmp_path / "attack-report.json"
    code = (
        "import json, pathlib\n"
        f"value = pathlib.Path({str(secret_file)!r}).read_text(encoding='utf-8')\n"
        f"pathlib.Path({str(report)!r}).write_text(json.dumps({{'secret_read_succeeded': value == 's3cret-value'}}))\n"
    )
    return_code, result, _job = _run_job_contained_child(code, tmp_path)

    assert return_code == 0, result["stderr"]
    assert result["report"] == {"secret_read_succeeded": True}, (
        "unexpected: Job Object isolated same-user secret files; re-assess secret_isolation"
    )


def test_windows_job_containment_does_not_isolate_foundation_db_files(tmp_path: Path) -> None:
    import sqlite3

    db_path = tmp_path / "foundation.sqlite"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE foundation_data (value TEXT)")
    connection.execute("INSERT INTO foundation_data VALUES ('db-row-value')")
    connection.commit()
    connection.close()

    report = tmp_path / "attack-report.json"
    code = (
        "import json, pathlib, sqlite3\n"
        f"connection = sqlite3.connect({str(db_path)!r})\n"
        "rows = connection.execute('SELECT value FROM foundation_data').fetchall()\n"
        "connection.close()\n"
        f"pathlib.Path({str(report)!r}).write_text(json.dumps({{'db_read_succeeded': rows == [('db-row-value',)]}}))\n"
    )
    return_code, result, _job = _run_job_contained_child(code, tmp_path)

    assert return_code == 0, result["stderr"]
    assert result["report"] == {"db_read_succeeded": True}, (
        "unexpected: Job Object isolated Foundation DB files; re-assess foundation_db_isolation"
    )


def test_windows_job_contains_descendant_breakaway_attempt(tmp_path: Path) -> None:
    from ctypes import wintypes

    import ctypes

    report = tmp_path / "attack-report.json"
    code = (
        "import json, pathlib, subprocess, sys\n"
        "try:\n"
        "    grandchild = subprocess.Popen(\n"
        "        [sys.executable, '-c', 'import time; time.sleep(15)'],\n"
        "        creationflags=0x01000000,  # CREATE_BREAKAWAY_FROM_JOB\n"
        "        stdout=subprocess.DEVNULL,\n"
        "        stderr=subprocess.DEVNULL,\n"
        "        stdin=subprocess.DEVNULL,\n"
        "    )\n"
        "    outcome = {'created': True, 'pid': grandchild.pid, 'winerror': None}\n"
        "except OSError as error:\n"
        "    outcome = {'created': False, 'pid': None, 'winerror': error.winerror}\n"
        f"pathlib.Path({str(report)!r}).write_text(json.dumps(outcome))\n"
    )
    script = tmp_path / "attack-main.py"
    script.write_text(code, encoding="utf-8")
    process = process_host._spawn(
        [sys.executable, "-I", str(script)],
        tmp_path,
        process_host._minimal_environment(tmp_path),
    )
    job_handle = process_host._attach_windows_job(process)
    try:
        process_host._resume_windows_process(process.pid)
        process.communicate(timeout=30)
        deadline = time.monotonic() + 5
        while not report.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert report.exists(), "attack child did not report its breakaway attempt"
        outcome = json.loads(report.read_text(encoding="utf-8"))

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        if not outcome["created"]:
            # Creation denied outright (ERROR_ACCESS_DENIED) — containment holds.
            assert outcome["winerror"] == 5, outcome
            return

        grandchild_pid = outcome["pid"]
        members = process_host._query_windows_job_process_ids(kernel32, job_handle)
        assert grandchild_pid in members, (
            "descendant escaped the Skill Job via breakaway; containment is broken",
            members,
            grandchild_pid,
        )
        handle = kernel32.OpenProcess(0x00100000, False, grandchild_pid)
        assert handle, "reported grandchild was not running"
        kernel32.CloseHandle(handle)
    finally:
        process_host._close_windows_handle(job_handle)


def test_windows_minimal_skill_environment_omits_parent_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHULIU_TEST_TASK36_SECRET", "task36-s3cret-marker")
    report = tmp_path / "attack-report.json"
    code = (
        "import json, os, pathlib\n"
        "env = dict(os.environ)\n"
        f"pathlib.Path({str(report)!r}).write_text(json.dumps({{'secret_present': 'SHULIU_TEST_TASK36_SECRET' in env, 'keys': sorted(env)}}))\n"
    )
    return_code, result, _job = _run_job_contained_child(code, tmp_path)

    assert return_code == 0, result["stderr"]
    assert result["report"]["secret_present"] is False, (
        "parent Secret values leaked into the minimal Skill environment"
    )
    # The WorkBuddy execution sandbox injects its own keys into every child
    # process regardless of the requested env; they are absent on clean
    # Windows runners.  Every non-minimal key must belong to that known set.
    observed_extras = set(result["report"]["keys"]) - {
        "PATH", "HOME", "TMPDIR", "TMP", "TEMP", "PYTHONNOUSERSITE", "PYTHONUTF8"
    }
    assert observed_extras <= {
        "LSBOX_AUDIT_SHMEM",
        "LSBOX_SHMEM_NAME",
        "SANDBOX_IPC_TRACE_PARENT",
        "SANDBOX_IPC_TRACE_SESSION",
    }, observed_extras


def test_windows_job_kill_on_close_terminates_surviving_descendants(tmp_path: Path) -> None:
    from ctypes import wintypes

    import ctypes

    report = tmp_path / "attack-report.json"
    code = (
        "import json, os, pathlib, subprocess, sys\n"
        "grandchild = subprocess.Popen(\n"
        "    [sys.executable, '-c', 'import time; time.sleep(60)'],\n"
        "    stdout=subprocess.DEVNULL,\n"
        "    stderr=subprocess.DEVNULL,\n"
        "    stdin=subprocess.DEVNULL,\n"
        ")\n"
        f"pathlib.Path({str(report)!r}).write_text(json.dumps({{'grandchild_pid': grandchild.pid, 'child_pid': os.getpid()}}))\n"
    )
    script = tmp_path / "attack-main.py"
    script.write_text(code, encoding="utf-8")
    process = process_host._spawn(
        [sys.executable, "-I", str(script)],
        tmp_path,
        process_host._minimal_environment(tmp_path),
    )
    job_handle = process_host._attach_windows_job(process)
    try:
        process_host._resume_windows_process(process.pid)
        process.communicate(timeout=30)
        deadline = time.monotonic() + 5
        while not report.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert report.exists(), "attack child did not report the grandchild pid"
        grandchild_pid = json.loads(report.read_text(encoding="utf-8"))["grandchild_pid"]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x00100000, False, grandchild_pid)  # SYNCHRONIZE
        assert handle, "grandchild was not running before job close"
        try:
            assert kernel32.WaitForSingleObject(handle, 0) != 0, (
                "grandchild already exited before kill-on-close could be tested"
            )
        finally:
            kernel32.CloseHandle(handle)

        # Job kill-on-close: closing the last host handle terminates all members.
        process_host._close_windows_handle(job_handle)
        job_handle = None

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            handle = kernel32.OpenProcess(0x00100000, False, grandchild_pid)
            if not handle:
                break
            exited = kernel32.WaitForSingleObject(handle, 100) == 0
            kernel32.CloseHandle(handle)
            if exited:
                break
            time.sleep(0.05)
        else:
            pytest.fail("grandchild survived Job kill-on-close")
    finally:
        if job_handle is not None:
            process_host._close_windows_handle(job_handle)
