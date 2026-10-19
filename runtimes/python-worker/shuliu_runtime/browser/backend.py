"""Supervise the trusted, separately locked browser-use driver. Not an OS sandbox."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import threading
import time
from uuid import uuid4

from shuliu_runtime.skills.process_host import (
    _attach_windows_job, _drain_stderr, _minimal_environment, _read_stdout,
    _resume_windows_process, _spawn, _terminate_process_tree, _write_stdin,
)
from .verification import BrowserError


class BrowserUseBackend:
    def __init__(self, *, python_executable=None, executable_path=None):
        root = Path(__file__).resolve().parents[3] / "browser-driver" / ".venv"
        self.python = Path(python_executable or root / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        self.executable = str(executable_path) if executable_path else None
        self.process = None
        self.job = None
        self.stop = threading.Event()
        self.responses = queue.Queue(maxsize=32)
        self.writes = queue.Queue(maxsize=8)
        self.errors = queue.Queue(maxsize=1)
        self.threads = []

    def _start(self, profile):
        if not self.python.is_file():
            raise BrowserError("BROWSER_DRIVER_UNAVAILABLE")
        directory = Path(profile).resolve(strict=True)
        env = _minimal_environment(directory)
        # Windows Winsock/asyncio requires the OS installation directory.
        if os.name == "nt":
            env["SystemRoot"] = os.environ["SystemRoot"]
            env["USERPROFILE"] = str(directory)
            env["LOCALAPPDATA"] = str(directory / "AppData" / "Local")
            env["APPDATA"] = str(directory / "AppData" / "Roaming")
            Path(env["LOCALAPPDATA"]).mkdir(parents=True, exist_ok=True)
            Path(env["APPDATA"]).mkdir(parents=True, exist_ok=True)
        env.update(ANONYMIZED_TELEMETRY="false", BROWSER_USE_DISABLE_EXTENSIONS="1",
                   BROWSER_USE_LOGGING_LEVEL="critical", PYTHONIOENCODING="utf-8")
        self.process = _spawn([str(self.python), "-I", str(Path(__file__).with_name("driver.py"))], directory, env)
        try:
            if os.name == "nt":
                self.job = _attach_windows_job(self.process)
                _resume_windows_process(self.process.pid)
            for target, args in (
                (_read_stdout, (self.process.stdout, self.responses, self.stop)),
                (_drain_stderr, (self.process.stderr,)),
                (_write_stdin, (self.process.stdin.fileno(), self.writes, self.errors)),
            ):
                thread = threading.Thread(target=target, args=args, daemon=True)
                thread.start()
                self.threads.append(thread)
        except Exception:
            self.close()
            raise

    def request(self, operation, payload, *, timeout, cancel_event=None):
        deadline = time.monotonic() + timeout
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError()
            if operation == "start":
                if self.process is not None:
                    raise BrowserError("BROWSER_ALREADY_STARTED")
                self._start(payload["profile"])
                payload = {**payload, "executable_path": self.executable}
            if self.process is None or self.process.poll() is not None:
                raise ConnectionError()
            request_id = str(uuid4())
            frame = json.dumps({"id": request_id, "operation": operation, "payload": payload}, ensure_ascii=True).encode() + b"\n"
            if len(frame) > 65536:
                raise BrowserError("BROWSER_PROTOCOL_INVALID")
            self.writes.put_nowait(frame)
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError()
                if time.monotonic() >= deadline:
                    raise TimeoutError()
                if not self.errors.empty():
                    raise ConnectionError()
                try:
                    raw = self.responses.get(timeout=min(0.05, max(0.001, deadline - time.monotonic())))
                except queue.Empty:
                    if self.process.poll() is not None:
                        raise ConnectionError()
                    continue
                if not isinstance(raw, bytes):
                    raise ConnectionError()
                message = json.loads(raw)
                if message.get("id") != request_id:
                    raise BrowserError("BROWSER_PROTOCOL_INVALID")
                if message.get("error"):
                    # Never propagate upstream exceptions, page text or driver stderr.
                    code = message["error"]
                    raise BrowserError(code if code in {"BROWSER_TARGET_DENIED", "BROWSER_TARGET_CHANGED", "BROWSER_ACCOUNT_CHANGED",
                        "BROWSER_ELEMENT_STALE", "BROWSER_ELEMENT_OBSCURED", "BROWSER_SENSITIVE_ELEMENT", "BROWSER_INPUT_UNSUPPORTED"}
                        else "BROWSER_CONNECTION_LOST")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise BrowserError("BROWSER_PROTOCOL_INVALID")
                if operation == "start":
                    self.browser_pid = result["browser_pid"]
                return result
        except Exception:
            self.close()
            raise

    def close(self):
        self.stop.set()
        process, self.process = self.process, None
        if process is None:
            return
        if os.name == "nt" and self.job is not None:
            _stop_windows_job(self.job)
        _terminate_process_tree(process, self.job)
        self.job = None
        self.writes.put_nowait(None)
        for thread in self.threads:
            thread.join(timeout=1)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _stop_windows_job(handle):
    """Wait for every owned Chrome descendant, not just the Python parent."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    class Accounting(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in ("user", "kernel", "period_user", "period_kernel")] + [
            (name, wintypes.DWORD) for name in ("faults", "total", "active", "terminated")]
    if not kernel.TerminateJobObject(handle, 1):
        raise BrowserError("BROWSER_CLEANUP_FAILED")
    deadline = time.monotonic() + 5
    while True:
        info = Accounting()
        if not kernel.QueryInformationJobObject(handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            raise BrowserError("BROWSER_CLEANUP_FAILED")
        if info.active == 0:
            return
        if time.monotonic() >= deadline:
            raise BrowserError("BROWSER_CLEANUP_FAILED")
        time.sleep(0.02)
