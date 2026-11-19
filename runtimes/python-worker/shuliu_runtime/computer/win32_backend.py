from __future__ import annotations

import ctypes
import os
import platform
import socket
from collections.abc import Callable, Mapping
from ctypes import wintypes

from .verification import ComputerError


class Win32Backend:
    """Windows session and top-level window adapter; never dispatches actions."""

    def __init__(self, *, session_query: Callable[[], Mapping[str, object]] | None = None):
        self._session_query = session_query or _query_interactive_session

    def capability(self) -> dict[str, object]:
        if os.name != "nt":
            return _unavailable("COMPUTER_WINDOWS_ONLY")
        try:
            session = dict(self._session_query())
        except Exception:
            return _unavailable("COMPUTER_SESSION_STATE_UNKNOWN")
        session_id = session.get("session_id")
        desktop_name = session.get("desktop_name")
        reason_code = _safe_code(session.get("reason_code"))
        if session.get("status") != "AVAILABLE":
            return _unavailable(reason_code) | {
                "session_id": session_id,
                "desktop_name": desktop_name,
                "machine": socket.gethostname(),
            }
        if not isinstance(session_id, int) or isinstance(session_id, bool) or not _is_interactive_session_id(session_id):
            return _unavailable("COMPUTER_SESSION_NOT_INTERACTIVE") | {
                "session_id": session_id,
                "desktop_name": desktop_name,
                "machine": socket.gethostname(),
            }
        if not isinstance(desktop_name, str):
            return _unavailable("COMPUTER_SESSION_STATE_UNKNOWN") | {
                "session_id": session_id,
                "desktop_name": None,
                "machine": socket.gethostname(),
            }
        if desktop_name.casefold() != "default":
            return _unavailable("COMPUTER_SESSION_LOCKED") | {
                "session_id": session_id,
                "desktop_name": desktop_name,
                "machine": socket.gethostname(),
            }
        return {
            "status": "AVAILABLE",
            "os": "windows",
            "reason_code": None,
            "session_id": session_id,
            "desktop_name": desktop_name,
            "machine": socket.gethostname(),
        }

    def foreground_window_ref(self) -> str:
        """Return only the current foreground HWND after validating its session."""
        if os.name != "nt":
            raise ComputerError("COMPUTER_WINDOWS_ONLY")
        try:
            import win32gui

            hwnd = int(win32gui.GetForegroundWindow())
            if hwnd <= 0:
                raise ComputerError("COMPUTER_WINDOW_UNAVAILABLE")
            window_ref = f"0x{hwnd:x}"
            self.inspect_window(window_ref)
            return window_ref
        except ComputerError:
            raise
        except Exception:
            raise ComputerError("COMPUTER_WINDOW_UNAVAILABLE") from None

    def inspect_window(self, window_ref: str) -> dict[str, object]:
        if os.name != "nt":
            raise ComputerError("COMPUTER_WINDOWS_ONLY")
        hwnd = _parse_window_ref(window_ref)
        try:
            import win32gui
            import win32process

            if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                raise ComputerError("COMPUTER_WINDOW_UNAVAILABLE")
            if win32gui.IsIconic(hwnd):
                raise ComputerError("COMPUTER_WINDOW_MINIMIZED")
            _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
            if process_id <= 0:
                raise ComputerError("COMPUTER_WINDOW_UNAVAILABLE")
            session_id = _process_session_id(process_id)
            capability = self.capability()
            if (
                capability.get("status") != "AVAILABLE"
                or session_id != capability.get("session_id")
            ):
                raise ComputerError("COMPUTER_SESSION_UNAVAILABLE")
            bounds = list(win32gui.GetWindowRect(hwnd))
            if len(bounds) != 4 or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                raise ComputerError("COMPUTER_WINDOW_UNAVAILABLE")
            dpi = _window_dpi(hwnd)
            if dpi is None:
                raise ComputerError("COMPUTER_DPI_UNAVAILABLE")
            return {
                "window_ref": window_ref,
                "hwnd": hwnd,
                "process_id": process_id,
                "title": win32gui.GetWindowText(hwnd)[:512],
                "bounds": bounds,
                "dpi": dpi,
            }
        except ComputerError:
            raise
        except Exception:
            raise ComputerError("COMPUTER_WINDOW_UNAVAILABLE") from None


def _query_interactive_session() -> dict[str, object]:
    if os.name != "nt":
        return {"status": "UNAVAILABLE", "reason_code": "COMPUTER_WINDOWS_ONLY"}
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
        user32 = ctypes.WinDLL("user32", use_last_error=True)

        kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
        wtsapi32.WTSQuerySessionInformationW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
            ctypes.POINTER(wintypes.DWORD),
        ]
        wtsapi32.WTSQuerySessionInformationW.restype = wintypes.BOOL
        wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]
        wtsapi32.WTSFreeMemory.restype = None
        user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        user32.OpenInputDesktop.restype = wintypes.HANDLE
        user32.GetUserObjectInformationW.argtypes = [
            wintypes.HANDLE,
            wintypes.INT,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetUserObjectInformationW.restype = wintypes.BOOL
        user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        user32.CloseDesktop.restype = wintypes.BOOL

        session_id = wintypes.DWORD()
        if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
            return {"status": "UNAVAILABLE", "reason_code": "COMPUTER_SESSION_STATE_UNKNOWN"}
        if not _is_interactive_session_id(int(session_id.value)):
            return {
                "status": "UNAVAILABLE",
                "reason_code": "COMPUTER_SESSION_NOT_INTERACTIVE",
                "session_id": int(session_id.value),
            }

        buffer = ctypes.POINTER(ctypes.c_ubyte)()
        byte_count = wintypes.DWORD()
        queried = wtsapi32.WTSQuerySessionInformationW(
            None,
            session_id.value,
            8,  # WTSConnectState
            ctypes.byref(buffer),
            ctypes.byref(byte_count),
        )
        if not queried:
            return {
                "status": "UNAVAILABLE",
                "reason_code": "COMPUTER_SESSION_STATE_UNKNOWN",
                "session_id": int(session_id.value),
            }
        try:
            if byte_count.value < ctypes.sizeof(ctypes.c_int):
                return {
                    "status": "UNAVAILABLE",
                    "reason_code": "COMPUTER_SESSION_STATE_UNKNOWN",
                    "session_id": int(session_id.value),
                }
            connect_state = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int)).contents.value
        finally:
            wtsapi32.WTSFreeMemory(buffer)
        if connect_state != 0:  # WTSActive
            return {
                "status": "UNAVAILABLE",
                "reason_code": "COMPUTER_SESSION_NOT_CONNECTED",
                "session_id": int(session_id.value),
            }

        desktop = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
        if not desktop:
            return {
                "status": "UNAVAILABLE",
                "reason_code": "COMPUTER_INPUT_DESKTOP_UNAVAILABLE",
                "session_id": int(session_id.value),
            }
        try:
            name_buffer = ctypes.create_unicode_buffer(128)
            needed = wintypes.DWORD()
            if not user32.GetUserObjectInformationW(
                desktop, 2, ctypes.cast(name_buffer, ctypes.c_void_p),
                ctypes.sizeof(name_buffer), ctypes.byref(needed)
            ):
                return {
                    "status": "UNAVAILABLE",
                    "reason_code": "COMPUTER_SESSION_STATE_UNKNOWN",
                    "session_id": int(session_id.value),
                }
            desktop_name = name_buffer.value
        finally:
            user32.CloseDesktop(desktop)
        if desktop_name.casefold() != "default":
            return {
                "status": "UNAVAILABLE",
                "reason_code": "COMPUTER_SESSION_LOCKED",
                "session_id": int(session_id.value),
                "desktop_name": desktop_name,
            }
        return {
            "status": "AVAILABLE",
            "session_id": int(session_id.value),
            "desktop_name": desktop_name,
        }
    except Exception:
        return {"status": "UNAVAILABLE", "reason_code": "COMPUTER_SESSION_STATE_UNKNOWN"}


def _process_session_id(process_id: int) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
    session_id = wintypes.DWORD()
    if not kernel32.ProcessIdToSessionId(process_id, ctypes.byref(session_id)):
        raise ComputerError("COMPUTER_SESSION_STATE_UNKNOWN")
    return int(session_id.value)


def _is_interactive_session_id(session_id: int) -> bool:
    """Reject services and the WTS no-session sentinel, while allowing active RDP."""
    return isinstance(session_id, int) and not isinstance(session_id, bool) and 0 < session_id < 0xFFFFFFFF


def _window_dpi(hwnd: int) -> int | None:
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get_dpi = user32.GetDpiForWindow
        get_dpi.argtypes = [wintypes.HWND]
        get_dpi.restype = wintypes.UINT
        dpi = int(get_dpi(hwnd))
        return dpi if dpi > 0 else None
    except Exception:
        return None


def _parse_window_ref(value: str) -> int:
    if not isinstance(value, str) or len(value) > 32:
        raise ComputerError("COMPUTER_WINDOW_REF_INVALID")
    try:
        hwnd = int(value, 0)
    except ValueError:
        raise ComputerError("COMPUTER_WINDOW_REF_INVALID") from None
    if hwnd <= 0:
        raise ComputerError("COMPUTER_WINDOW_REF_INVALID")
    return hwnd


def _safe_code(value: object) -> str:
    return value if isinstance(value, str) and value.startswith("COMPUTER_") else "COMPUTER_SESSION_UNAVAILABLE"


def _unavailable(reason_code: str) -> dict[str, object]:
    return {
        "status": "UNAVAILABLE",
        "os": "windows" if os.name == "nt" else platform.system().lower(),
        "reason_code": reason_code,
        "session_id": None,
        "desktop_name": None,
        "machine": socket.gethostname() if os.name == "nt" else None,
    }
