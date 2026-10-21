from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from shuliu_runtime.skills import process_host
from shuliu_runtime.skills.process_host import SkillProcessError, SkillProcessHost
from shuliu_runtime.skills.sandbox import SandboxPolicy, UnavailableSandboxProvider
from tests.fakes.fake_skill_sandbox import TestOnlyProcessProvider


def test_windows_job_cleanup_waits_for_late_job_members(monkeypatch: pytest.MonkeyPatch):
    job_handle = 101
    captured_process_handle = 202
    late_process_handle = 303
    calls: list[tuple[str, int, int | None]] = []
    state = {"active_process_ids": [202], "termination_calls": 0}

    class FakeFunction:
        def __init__(self, callback):
            self.callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.callback(*args)

    def terminate_job(handle, exit_code):
        assert handle == job_handle
        assert exit_code == 1
        state["termination_calls"] += 1
        calls.append(("terminate-job", handle, None))
        if state["termination_calls"] == 2:
            state["active_process_ids"] = []
        return 1

    def query_process_ids(_kernel32, handle):
        assert handle == job_handle
        return list(state["active_process_ids"])

    def open_process_handles(_kernel32, handle):
        assert handle == job_handle
        if state["active_process_ids"] == [202]:
            return [captured_process_handle]
        if state["active_process_ids"] == [303]:
            return [late_process_handle]
        return []

    def wait_for_object(_kernel32, handle, timeout):
        assert timeout != 0xFFFFFFFF
        calls.append(("wait", handle, timeout))
        if handle == captured_process_handle:
            # A descendant joins after TerminateJobObject has returned but
            # before cleanup takes its next Job membership snapshot.
            state["active_process_ids"] = [303]
            return 0
        if handle == late_process_handle:
            return 0 if not state["active_process_ids"] else 258
        assert handle == job_handle
        return 0 if state["termination_calls"] == 2 else 258

    class FakeKernel32:
        pass

    kernel32 = FakeKernel32()
    kernel32.TerminateJobObject = FakeFunction(terminate_job)
    kernel32.WaitForSingleObject = FakeFunction(wait_for_object)

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32)
    monkeypatch.setattr(
        process_host,
        "_open_windows_job_process_handles",
        open_process_handles,
    )
    monkeypatch.setattr(
        process_host,
        "_query_windows_job_process_ids",
        query_process_ids,
    )
    monkeypatch.setattr(
        process_host,
        "_wait_windows_process_handle",
        wait_for_object,
    )
    monkeypatch.setattr(
        process_host,
        "_close_windows_process_handles",
        lambda _kernel32, handles: calls.append(("close", handles[0], None)),
    )

    process_host._terminate_windows_job(job_handle)

    assert state["termination_calls"] == 2
    assert not state["active_process_ids"]
    assert calls[0] == ("terminate-job", job_handle, None)
    waits = [call for call in calls if call[0] == "wait"]
    assert [call[1] for call in waits] == [
        captured_process_handle,
        job_handle,
        late_process_handle,
        job_handle,
    ]
    assert all(call[2] != 0xFFFFFFFF for call in waits)
    assert calls[-1] == ("close", late_process_handle, None)


def test_windows_job_cleanup_rescans_member_created_during_termination_call(
    monkeypatch: pytest.MonkeyPatch,
):
    job_handle = 101
    captured_process_handle = 202
    late_process_handle = 303
    calls: list[tuple[str, int, int | None]] = []
    state = {"active_process_ids": [202], "termination_calls": 0}

    class FakeFunction:
        def __init__(self, callback):
            self.callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.callback(*args)

    def terminate_job(handle, exit_code):
        assert handle == job_handle
        assert exit_code == 1
        state["termination_calls"] += 1
        calls.append(("terminate", handle, None))
        if state["termination_calls"] == 1:
            # Simulate a descendant joining after the supervisor entered the
            # termination call but before the kernel request takes effect.
            state["active_process_ids"] = [303]
        else:
            state["active_process_ids"] = []
        return 1

    class FakeKernel32:
        TerminateJobObject = FakeFunction(terminate_job)

    kernel32 = FakeKernel32()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32)

    def query_process_ids(_kernel32: Any, handle: int) -> list[int]:
        assert handle == job_handle
        return list(state["active_process_ids"])

    def open_process_handles(_kernel32: Any, handle: int) -> list[int]:
        assert handle == job_handle
        if state["active_process_ids"] == [303]:
            calls.append(("snapshot-late-member", late_process_handle, None))
            return [late_process_handle]
        if state["termination_calls"] == 0:
            calls.append(("snapshot-original-member", captured_process_handle, None))
            return [captured_process_handle]
        return []

    def wait_for_object(_kernel32: Any, handle: int, timeout: int) -> int:
        assert timeout != 0xFFFFFFFF, "cleanup must use a bounded wait"
        calls.append(("wait", handle, timeout))
        if handle == captured_process_handle:
            return 0
        if handle == late_process_handle:
            return 0 if not state["active_process_ids"] else 258
        if handle == job_handle:
            return 0 if state["termination_calls"] == 2 else 258
        pytest.fail("cleanup must not wait forever on the Job handle")

    monkeypatch.setattr(
        process_host, "_query_windows_job_process_ids", query_process_ids
    )
    monkeypatch.setattr(
        process_host, "_open_windows_job_process_handles", open_process_handles
    )
    monkeypatch.setattr(
        process_host, "_wait_windows_process_handle", wait_for_object
    )
    monkeypatch.setattr(
        process_host,
        "_close_windows_process_handles",
        lambda _kernel32, handles: calls.append(("close", handles[0], None)),
    )

    process_host._terminate_windows_job(job_handle)

    assert state["termination_calls"] == 2
    assert not state["active_process_ids"]
    assert ("snapshot-late-member", late_process_handle, None) in calls
    assert calls[-1] == ("close", late_process_handle, None)


def test_windows_empty_job_cleanup_returns_without_waiting_for_job_signal(
    monkeypatch: pytest.MonkeyPatch,
):
    job_handle = 101
    calls: list[str] = []

    class FakeFunction:
        def __init__(self, callback):
            self.callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.callback(*args)

    class FakeKernel32:
        TerminateJobObject = FakeFunction(
            lambda *_args: calls.append("terminate") or 1
        )

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: FakeKernel32())
    monkeypatch.setattr(
        process_host, "_query_windows_job_process_ids", lambda *_args: []
    )
    monkeypatch.setattr(
        process_host, "_open_windows_job_process_handles", lambda *_args: []
    )

    def unexpected_wait(*_args):
        pytest.fail("an empty Job must not be waited on for a signal it may never get")

    monkeypatch.setattr(process_host, "_wait_windows_process_handle", unexpected_wait)

    process_host._terminate_windows_job(job_handle)

    assert calls == []


def test_windows_job_cleanup_timeout_does_not_report_live_member_as_terminated(
    monkeypatch: pytest.MonkeyPatch,
):
    job_handle = 101
    process_handle = 202
    calls: list[tuple[str, int, int | None]] = []

    class FakeFunction:
        def __init__(self, callback):
            self.callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.callback(*args)

    class FakeKernel32:
        TerminateJobObject = FakeFunction(lambda *_args: 1)

    def wait_for_object(_kernel32: Any, handle: int, timeout: int) -> int:
        calls.append(("wait", handle, timeout))
        assert timeout != 0xFFFFFFFF, "cleanup must use a bounded wait"
        assert handle == process_handle
        return 258  # WAIT_TIMEOUT: this member is still running.

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: FakeKernel32())
    monkeypatch.setattr(
        process_host,
        "_query_windows_job_process_ids",
        lambda *_args: [303],
    )
    monkeypatch.setattr(
        process_host,
        "_open_windows_job_process_handles",
        lambda *_args: [process_handle],
    )
    monkeypatch.setattr(process_host, "_wait_windows_process_handle", wait_for_object)
    monkeypatch.setattr(
        process_host,
        "_close_windows_process_handles",
        lambda *_args: None,
    )

    with pytest.raises(TimeoutError):
        process_host._terminate_windows_job(job_handle)

    assert calls
    assert all(timeout is not None and timeout < 0xFFFFFFFF for _, _, timeout in calls)


def test_windows_job_cleanup_does_not_trust_empty_membership_before_job_signals(
    monkeypatch: pytest.MonkeyPatch,
):
    job_handle = 101
    process_handle = 202
    waits: list[tuple[int, int]] = []
    state = {"process_ids": [303]}

    class FakeFunction:
        def __init__(self, callback):
            self.callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.callback(*args)

    def terminate_job(handle, exit_code):
        assert handle == job_handle
        assert exit_code == 1
        # Simulate the membership list reaching zero before the job's
        # termination-completion signal is observable.
        state["process_ids"] = []
        return 1

    class FakeKernel32:
        TerminateJobObject = FakeFunction(terminate_job)

    def query_process_ids(_kernel32: Any, handle: int) -> list[int]:
        assert handle == job_handle
        return list(state["process_ids"])

    def wait_for_object(_kernel32: Any, handle: int, timeout: int) -> int:
        assert timeout != 0xFFFFFFFF, "job completion wait must be bounded"
        waits.append((handle, timeout))
        if handle == process_handle:
            return 0
        assert handle == job_handle
        return 258  # WAIT_TIMEOUT: termination completion is not confirmed.

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: FakeKernel32())
    monkeypatch.setattr(process_host, "_query_windows_job_process_ids", query_process_ids)
    monkeypatch.setattr(
        process_host,
        "_open_windows_job_process_handles",
        lambda *_args: [process_handle],
    )
    monkeypatch.setattr(process_host, "_wait_windows_process_handle", wait_for_object)
    monkeypatch.setattr(
        process_host,
        "_close_windows_process_handles",
        lambda *_args: None,
    )
    monkeypatch.setattr(process_host, "_WINDOWS_JOB_CLEANUP_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(
        process_host,
        "_open_windows_job_process_handles",
        lambda _kernel32, _job: [process_handle]
        if state["process_ids"]
        else [],
    )

    with pytest.raises(TimeoutError, match="completion"):
        process_host._terminate_windows_job(job_handle)

    assert [handle for handle, _ in waits][:2] == [process_handle, job_handle]
    assert all(handle == job_handle for handle, _ in waits[1:])
    assert state["process_ids"] == []


def test_windows_job_termination_failure_does_not_wait_forever(
    monkeypatch: pytest.MonkeyPatch,
):
    job_handle = 101
    captured_process_handle = 202
    calls: list[tuple[str, int, int | None]] = []

    class FakeFunction:
        def __init__(self, callback):
            self.callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.callback(*args)

    class FakeKernel32:
        TerminateJobObject = FakeFunction(
            lambda handle, _exit_code: calls.append(("terminate", handle, None)) or 0
        )

    kernel32 = FakeKernel32()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5)
    monkeypatch.setattr(
        process_host,
        "_open_windows_job_process_handles",
        lambda _kernel32, _job: [captured_process_handle],
    )
    monkeypatch.setattr(
        process_host,
        "_query_windows_job_process_ids",
        lambda *_args: [303],
    )
    monkeypatch.setattr(
        process_host,
        "_close_windows_process_handles",
        lambda _kernel32, handles: calls.append(("close", handles[0], None)),
    )

    with pytest.raises(OSError):
        process_host._terminate_windows_job(job_handle)

    assert calls[-1] == ("close", captured_process_handle, None)


def test_windows_job_termination_failure_with_disappearing_pid_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
):
    job_handle = 101
    waits: list[tuple[int, int]] = []
    state = {"process_ids": [303]}

    class FakeFunction:
        def __init__(self, callback):
            self.callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.callback(*args)

    def terminate_job(handle, exit_code):
        assert handle == job_handle
        assert exit_code == 1
        state["process_ids"] = []
        return 0

    class FakeKernel32:
        TerminateJobObject = FakeFunction(terminate_job)

    def query_process_ids(_kernel32: Any, handle: int) -> list[int]:
        assert handle == job_handle
        return list(state["process_ids"])

    def wait_for_object(_kernel32: Any, handle: int, timeout: int) -> int:
        assert handle == job_handle
        assert timeout != 0xFFFFFFFF, "failed termination must use a bounded wait"
        waits.append((handle, timeout))
        return 258  # WAIT_TIMEOUT: no completion signal proves cleanup.

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: FakeKernel32())
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5)
    monkeypatch.setattr(process_host, "_query_windows_job_process_ids", query_process_ids)
    monkeypatch.setattr(
        process_host,
        "_open_windows_job_process_handles",
        lambda *_args: [],
    )
    monkeypatch.setattr(process_host, "_wait_windows_process_handle", wait_for_object)
    monkeypatch.setattr(
        process_host,
        "_close_windows_process_handles",
        lambda *_args: None,
    )
    monkeypatch.setattr(process_host, "_WINDOWS_JOB_CLEANUP_TIMEOUT_SECONDS", 0.03)

    with pytest.raises(TimeoutError, match="completion"):
        process_host._terminate_windows_job(job_handle)

    assert state["process_ids"] == []
    assert waits
    assert all(handle == job_handle for handle, _ in waits)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner only")
def test_windows_without_verified_provider_fails_closed():
    assessment = UnavailableSandboxProvider().assess(
        SandboxPolicy(skill_id="platform-check", permissions=())
    )

    assert assessment.supported is False
    assert assessment.enforced_controls == frozenset()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner only")
def test_windows_skill_spawn_requests_suspended_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    captured: dict[str, object] = {}
    process = object()

    def capture_popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return process

    monkeypatch.setattr(process_host.subprocess, "Popen", capture_popen)

    spawned = process_host._spawn(["skill.exe"], tmp_path, {})

    assert spawned is process
    creation_flags = int(captured["creationflags"])
    assert creation_flags & process_host._WINDOWS_CREATE_SUSPENDED
    assert creation_flags & subprocess.CREATE_NEW_PROCESS_GROUP
    assert not creation_flags & 0x01000000  # CREATE_BREAKAWAY_FROM_JOB


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner only")
@pytest.mark.parametrize(
    "termination_mode",
    [
        "normal",
        "crash",
        "cancel",
        "spawn-after-snapshot",
        "spawn-during-termination-call",
    ],
)
def test_windows_job_is_assigned_before_skill_can_spawn_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, termination_mode: str
):
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.SetEvent.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    kernel32.IsProcessInJob.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.INT,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.GetHandleInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetHandleInformation.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    event_name = f"Local\\ShuLiuSkillJob-{uuid.uuid4()}"
    release_event = kernel32.CreateEventW(None, True, False, event_name)
    assert release_event
    spawn_event_name = f"Local\\ShuLiuSkillSpawn-{uuid.uuid4()}"
    spawn_event = kernel32.CreateEventW(None, True, False, spawn_event_name)
    assert spawn_event
    late_spawned_event_name = f"Local\\ShuLiuSkillLateSpawn-{uuid.uuid4()}"
    late_spawned_event = kernel32.CreateEventW(
        None, True, False, late_spawned_event_name
    )
    assert late_spawned_event
    descendant_directory = tmp_path / "descendants"
    descendant_directory.mkdir()
    descendant_count = 8
    late_report = descendant_directory / "late-descendant.json"
    late_released = descendant_directory / "late-descendant.released"
    descendant_reports = [
        descendant_directory / f"descendant-{index}.json"
        for index in range(descendant_count)
    ]
    descendant_released = [
        descendant_directory / f"descendant-{index}.released"
        for index in range(descendant_count)
    ]
    descendant_errors = [
        descendant_directory / f"descendant-{index}.error"
        for index in range(descendant_count)
    ]
    package = tmp_path / "windows-skill"
    package.mkdir()
    script = package / "main.py"
    late_child_code = f"""
import ctypes, json, os, pathlib
from ctypes import wintypes
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.GetCurrentProcess.restype = wintypes.HANDLE
kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
kernel.IsProcessInJob.restype = wintypes.BOOL
in_job = wintypes.BOOL()
if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(in_job)):
    raise OSError('job-membership-query-failed')
pathlib.Path({str(late_report)!r}).write_text(json.dumps({{'pid': os.getpid(), 'in_job': bool(in_job.value)}}), encoding='utf-8')
kernel.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel.OpenEventW.restype = wintypes.HANDLE
release = kernel.OpenEventW(0x00100000, False, {event_name!r})
if not release:
    raise OSError('release-event-unavailable')
kernel.SetEvent.argtypes = [wintypes.HANDLE]
kernel.SetEvent.restype = wintypes.BOOL
spawned = kernel.OpenEventW(0x0002, False, {late_spawned_event_name!r})
if not spawned:
    raise OSError('late-spawn-event-unavailable')
kernel.SetEvent(spawned)
kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel.WaitForSingleObject.restype = wintypes.DWORD
kernel.WaitForSingleObject(release, 0xFFFFFFFF)
pathlib.Path({str(late_released)!r}).write_text('released', encoding='utf-8')
"""
    child_code = f"""
import ctypes, json, os, pathlib, subprocess, sys
from ctypes import wintypes
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
report = pathlib.Path({str(descendant_directory)!r}, 'descendant-' + sys.argv[1] + '.json')
error_report = pathlib.Path({str(descendant_directory)!r}, 'descendant-' + sys.argv[1] + '.error')
stage = 'open-event'
try:
    kernel.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenEventW.restype = wintypes.HANDLE
    release = kernel.OpenEventW(0x00100000, False, {event_name!r})
    if not release:
        raise OSError('release-event-unavailable')
    stage = 'query-job-membership'
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    in_job = wintypes.BOOL()
    if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(in_job)):
        raise OSError('job-membership-query-failed')
    stage = 'write-started-report'
    report.write_text(json.dumps({{'pid': os.getpid(), 'in_job': bool(in_job.value)}}), encoding='utf-8')
    if {termination_mode!r} in ('spawn-after-snapshot', 'spawn-during-termination-call') and sys.argv[1] == '0':
        spawn = kernel.OpenEventW(0x00100000, False, {spawn_event_name!r})
        if not spawn:
            raise OSError('spawn-event-unavailable')
        if kernel.WaitForSingleObject(spawn, 0xFFFFFFFF) != 0:
            raise OSError('spawn-event-wait-failed')
        subprocess.Popen([sys.executable, '-c', {late_child_code!r}])
    stage = 'wait-for-release'
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.WaitForSingleObject(release, 0xFFFFFFFF)
    pathlib.Path({str(descendant_directory)!r}, 'descendant-' + sys.argv[1] + '.released').write_text('released', encoding='utf-8')
except BaseException as error:
    error_report.write_text(f'{{stage}}:{{type(error).__name__}}:{{ctypes.get_last_error()}}', encoding='utf-8')
    raise
"""
    script.write_text(
        "import json, os, pathlib, subprocess, sys, time\n"
        f"for index in range({descendant_count}):\n"
        f"    subprocess.Popen([sys.executable, '-c', {child_code!r}, str(index)])\n"
        "deadline = time.monotonic() + 4\n"
        f"while len(list(pathlib.Path({str(descendant_directory)!r}).glob('*.json'))) < {descendant_count} and time.monotonic() < deadline:\n"
        "    time.sleep(0.005)\n"
        f"if len(list(pathlib.Path({str(descendant_directory)!r}).glob('*.json'))) < {descendant_count}:\n"
        "    raise RuntimeError('descendant-start-handshake-failed')\n"
        f"if {termination_mode!r} == 'crash':\n"
        "    os.abort()\n"
        f"elif {termination_mode!r} in ('normal', 'spawn-after-snapshot', 'spawn-during-termination-call'):\n"
        "    print(json.dumps({'type':'skill.result','output':{'ok':True}}), flush=True)\n"
        "else:\n"
        "    print(json.dumps({'type':'skill.progress','output':{'ready':True}}), flush=True)\n"
        "    time.sleep(30)\n",
        encoding="utf-8",
    )

    original_attach = process_host._attach_windows_job
    original_resume = process_host._resume_windows_process
    original_terminate = process_host._terminate_process_tree
    original_open_job_process_handles = process_host._open_windows_job_process_handles
    original_close_job_handle = process_host._close_windows_handle
    original_windll = ctypes.WinDLL
    lifecycle: list[str] = []
    membership_before_cleanup: list[tuple[int, bool]] = []
    job_limit_flags_before_cleanup: list[int] = []
    active_processes_before_cleanup: list[int] = []
    job_process_ids_before_cleanup: list[set[int]] = []
    job_handle_close_checks: list[tuple[int, bool, int]] = []
    child_wait_handles: list[tuple[int, Any]] = []
    cleanup_wait_results: list[tuple[int, int]] = []
    late_membership: list[tuple[int, bool]] = []
    termination_interposer = {"enabled": False, "used": False}

    class TerminateJobObjectInterposer:
        def __init__(self, native):
            self.native = native
            self.argtypes = None
            self.restype = None

        def __call__(self, job_handle_for_call, exit_code):
            termination_interposer["used"] = True
            lifecycle.append("terminate-call-entered")
            assert kernel32.SetEvent(spawn_event)
            assert kernel32.WaitForSingleObject(late_spawned_event, 6000) == 0
            assert late_report.exists(), "late descendant did not start during termination"
            late_pid = json.loads(late_report.read_text(encoding="utf-8"))["pid"]
            late_handle = kernel32.OpenProcess(0x00101000, False, late_pid)
            assert late_handle, ctypes.get_last_error()
            in_skill_job = wintypes.BOOL()
            assert kernel32.IsProcessInJob(
                late_handle, job_handle_for_call, ctypes.byref(in_skill_job)
            )
            late_membership.append((late_pid, bool(in_skill_job.value)))
            child_wait_handles.append((late_pid, late_handle))
            return self.native(job_handle_for_call, exit_code)

    class Kernel32TerminationProxy:
        def __init__(self, native):
            self.native = native
            self.native.TerminateJobObject.argtypes = [
                wintypes.HANDLE,
                wintypes.UINT,
            ]
            self.native.TerminateJobObject.restype = wintypes.BOOL
            self.TerminateJobObject = TerminateJobObjectInterposer(
                native.TerminateJobObject
            )

        def __getattr__(self, name):
            return getattr(self.native, name)

    def interposed_windll(name, *args, **kwargs):
        native = original_windll(name, *args, **kwargs)
        if (
            name.lower() == "kernel32"
            and termination_mode == "spawn-during-termination-call"
            and termination_interposer["enabled"]
            and not termination_interposer["used"]
        ):
            return Kernel32TerminationProxy(native)
        return native

    monkeypatch.setattr(ctypes, "WinDLL", interposed_windll)

    def record_job_assignment(process):
        handle = original_attach(process)
        lifecycle.append("job_assigned")
        return handle

    def assert_job_assigned_before_resume(process_id: int):
        assert lifecycle == ["job_assigned"]
        lifecycle.append("resumed")
        return original_resume(process_id)

    def record_exact_job_membership(process, job_handle):
        active_processes_before_cleanup.append(
            process_host._query_windows_job_active_process_count(kernel32, job_handle)
        )
        class BasicProcessIdList(ctypes.Structure):
            _fields_ = [
                ("NumberOfAssignedProcesses", wintypes.DWORD),
                ("NumberOfProcessIdsInList", wintypes.DWORD),
                ("ProcessIdList", ctypes.c_size_t * 64),
            ]

        process_list = BasicProcessIdList()
        returned = wintypes.DWORD()
        assert kernel32.QueryInformationJobObject(
            job_handle,
            3,
            ctypes.byref(process_list),
            ctypes.sizeof(process_list),
            ctypes.byref(returned),
        ), ctypes.get_last_error()
        job_process_ids_before_cleanup.append(
            set(
                int(pid)
                for pid in process_list.ProcessIdList[
                    : process_list.NumberOfProcessIdsInList
                ]
            )
        )
        limits = (ctypes.c_ubyte * 144)()
        returned = wintypes.DWORD()
        assert kernel32.QueryInformationJobObject(
            job_handle, 9, ctypes.byref(limits), ctypes.sizeof(limits), ctypes.byref(returned)
        ), ctypes.get_last_error()
        job_limit_flags_before_cleanup.append(
            ctypes.cast(ctypes.byref(limits, 16), ctypes.POINTER(wintypes.DWORD)).contents.value
        )
        for path in descendant_reports:
            if not path.exists():
                continue
            pid = json.loads(path.read_text(encoding="utf-8"))["pid"]
            child_handle = kernel32.OpenProcess(0x00101000, False, pid)
            assert child_handle, ctypes.get_last_error()
            in_skill_job = wintypes.BOOL()
            assert kernel32.IsProcessInJob(
                child_handle, job_handle, ctypes.byref(in_skill_job)
            )
            membership_before_cleanup.append((pid, bool(in_skill_job.value)))
            child_wait_handles.append((pid, child_handle))
        if termination_mode == "spawn-during-termination-call":
            termination_interposer["enabled"] = True
        result = original_terminate(process, job_handle)
        cleanup_wait_results.extend(
            (pid, kernel32.WaitForSingleObject(handle, 0))
            for pid, handle in child_wait_handles
        )
        for _, handle in child_wait_handles:
            kernel32.CloseHandle(handle)
        child_wait_handles.clear()
        return result

    def spawn_descendant_after_process_snapshot(kernel32_for_job, job_handle):
        process_handles = original_open_job_process_handles(
            kernel32_for_job, job_handle
        )
        if termination_mode == "spawn-after-snapshot":
            assert kernel32.SetEvent(spawn_event)
            deadline = time.monotonic() + 6
            while not late_report.exists() and time.monotonic() < deadline:
                time.sleep(0.005)
            assert late_report.exists(), "late descendant did not start after the Job snapshot"
            late_pid = json.loads(late_report.read_text(encoding="utf-8"))["pid"]
            late_handle = kernel32.OpenProcess(0x00101000, False, late_pid)
            assert late_handle, ctypes.get_last_error()
            in_skill_job = wintypes.BOOL()
            assert kernel32.IsProcessInJob(
                late_handle, job_handle, ctypes.byref(in_skill_job)
            )
            late_membership.append((late_pid, bool(in_skill_job.value)))
            child_wait_handles.append((late_pid, late_handle))
        return process_handles

    def verify_job_handle_closed(handle):
        original_close_job_handle(handle)
        handle_flags = wintypes.DWORD()
        remains_valid = bool(kernel32.GetHandleInformation(handle, ctypes.byref(handle_flags)))
        close_error = 0 if remains_valid else ctypes.get_last_error()
        job_handle_close_checks.append((int(handle), remains_valid, close_error))

    monkeypatch.setattr(
        process_host, "_attach_windows_job", record_job_assignment
    )
    monkeypatch.setattr(
        process_host,
        "_resume_windows_process",
        assert_job_assigned_before_resume,
    )
    monkeypatch.setattr(
        process_host,
        "_terminate_process_tree",
        record_exact_job_membership,
    )
    monkeypatch.setattr(
        process_host,
        "_open_windows_job_process_handles",
        spawn_descendant_after_process_snapshot,
    )
    monkeypatch.setattr(
        process_host, "_close_windows_handle", verify_job_handle_closed
    )
    pids: list[int] = []
    cancel_event = threading.Event()
    results: list[object] = []
    errors: list[BaseException] = []

    def run_skill():
        try:
            results.append(
                SkillProcessHost(TestOnlyProcessProvider()).run(
                    package,
                    SandboxPolicy(skill_id="windows-race", permissions=()),
                    {},
                    timeout=8,
                    cancel_event=cancel_event,
                )
            )
        except BaseException as error:  # noqa: BLE001 - assert worker outcome below
            errors.append(error)

    worker = threading.Thread(target=run_skill)
    try:
        worker.start()
        startup_deadline = time.monotonic() + 6
        while (
            not all(path.exists() for path in descendant_reports)
            and worker.is_alive()
            and time.monotonic() < startup_deadline
        ):
            time.sleep(0.005)
        if termination_mode == "cancel" and all(
            path.exists() for path in descendant_reports
        ):
            cancel_event.set()
        worker.join(timeout=15)
        assert not worker.is_alive(), "Skill host did not finish after termination"

        assert lifecycle[:2] == ["job_assigned", "resumed"]
        if termination_mode != "spawn-during-termination-call":
            assert lifecycle == ["job_assigned", "resumed"]
        assert not any(path.exists() for path in descendant_errors), [
            path.read_text(encoding="utf-8") for path in descendant_errors if path.exists()
        ]
        assert all(path.exists() for path in descendant_reports), (
            "descendants did not complete their startup handshake",
            [path.name for path in descendant_errors if path.exists()],
        )
        reports = [json.loads(path.read_text(encoding="utf-8")) for path in descendant_reports]
        pids = [report["pid"] for report in reports]
        if late_report.exists():
            pids.append(json.loads(late_report.read_text(encoding="utf-8"))["pid"])
        assert all(report["in_job"] for report in reports), reports
        assert sorted(membership_before_cleanup + late_membership) == sorted(
            (pid, True) for pid in pids
        )
        if termination_mode in {
            "spawn-after-snapshot",
            "spawn-during-termination-call",
        }:
            assert late_membership == [(pids[-1], True)]
        assert len(active_processes_before_cleanup) == 1
        assert active_processes_before_cleanup[0] == len(job_process_ids_before_cleanup[0])
        assert set(pids[:descendant_count]).issubset(job_process_ids_before_cleanup[0])
        if termination_mode == "spawn-after-snapshot":
            assert pids[-1] not in job_process_ids_before_cleanup[0]
        if termination_mode == "spawn-during-termination-call":
            assert pids[-1] not in job_process_ids_before_cleanup[0]
            assert "terminate-call-entered" in lifecycle
            assert termination_interposer["used"]
        assert job_limit_flags_before_cleanup == [0x00002000]
        assert sorted(cleanup_wait_results) == sorted((pid, 0) for pid in pids)
        assert len(job_handle_close_checks) == 1
        assert not job_handle_close_checks[0][1], job_handle_close_checks
        if termination_mode in {
            "normal",
            "spawn-after-snapshot",
            "spawn-during-termination-call",
        }:
            assert not errors
            assert len(results) == 1
            assert results[0].output == {"ok": True}
        else:
            assert not results
            assert len(errors) == 1
            assert isinstance(errors[0], SkillProcessError)
            expected_code = (
                "SKILL_EXECUTION_CANCELLED"
                if termination_mode == "cancel"
                else "SKILL_PROCESS_CRASHED"
            )
            assert errors[0].code == expected_code

        for pid in pids:
            handle = kernel32.OpenProcess(0x00100000, False, pid)
            if not handle:
                assert ctypes.get_last_error() == 87
                continue
            try:
                assert kernel32.WaitForSingleObject(handle, 0) == 0, (
                    "descendant remained alive after Job cleanup",
                    pid,
                )
            finally:
                kernel32.CloseHandle(handle)
        assert not any(path.exists() for path in descendant_released)
    finally:
        # Release any escaped test child so a failed assertion cannot leave it behind.
        kernel32.SetEvent(release_event)
        kernel32.SetEvent(spawn_event)
        if termination_mode == "spawn-after-snapshot":
            deadline = time.monotonic() + 6
            while not late_report.exists() and time.monotonic() < deadline:
                time.sleep(0.005)
        for path in descendant_reports:
            if path.exists():
                pid = json.loads(path.read_text(encoding="utf-8"))["pid"]
                if pid not in pids:
                    pids.append(pid)
        if late_report.exists():
            late_pid = json.loads(late_report.read_text(encoding="utf-8"))["pid"]
            if late_pid not in pids:
                pids.append(late_pid)
        for pid in pids:
            handle = kernel32.OpenProcess(0x00100001, False, pid)
            if handle:
                try:
                    if kernel32.WaitForSingleObject(handle, 0) == 0x102:
                        kernel32.TerminateProcess(handle, 137)
                        kernel32.WaitForSingleObject(handle, 2000)
                finally:
                    kernel32.CloseHandle(handle)
        for _, handle in child_wait_handles:
            kernel32.CloseHandle(handle)
        kernel32.CloseHandle(release_event)
        kernel32.CloseHandle(spawn_event)
        kernel32.CloseHandle(late_spawned_event)
