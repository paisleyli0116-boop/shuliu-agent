from pathlib import Path
import time
import pytest
from .support import call, make_runtime


def test_windows_long_profile_paths_are_removed(tmp_path):
    import os
    from pathlib import Path
    if os.name != "nt":
        pytest.skip("Windows extended path cleanup")
    runtime, backend, *_ = make_runtime(tmp_path)
    call(runtime, "launch")
    root = Path(backend.profiles[0]).resolve()
    child = root / ("a" * 90) / ("b" * 90)
    child.relative_to(tmp_path.resolve())
    extended = Path("\\\\?\\" + str(child))
    extended.mkdir(parents=True)
    (extended / "profile-data.txt").write_text("fixture")
    runtime.close()
    assert list(tmp_path.iterdir()) == []


def test_each_session_has_an_ephemeral_profile_and_an_authenticated_owner(tmp_path):
    runtime, backend, *_ = make_runtime(tmp_path)
    try:
        first = call(runtime, "launch").output["session_id"]
        second = call(runtime, "launch", actor="bob").output["session_id"]
        assert first != second
        denied = call(runtime, "observe", {"session_id": first}, actor="bob", subject_id="alice")
        assert denied.output["error_code"] == "BROWSER_SESSION_FORBIDDEN"
        assert denied.verification_status.value == "FAILED"
        assert len(list(tmp_path.iterdir())) == 2
        assert call(runtime, "close", {"session_id": first}).output["status"] == "VERIFIED"
        assert not Path(backend.profiles[0]).exists()
    finally:
        runtime.close()
    assert list(tmp_path.iterdir()) == []


def test_expired_session_is_closed_instead_of_reused(tmp_path):
    runtime, backend, *_ = make_runtime(tmp_path, ttl=0.01)
    try:
        session = call(runtime, "launch").output["session_id"]
        time.sleep(0.02)
        result = call(runtime, "observe", {"session_id": session})
        assert result.output["error_code"] == "BROWSER_SESSION_EXPIRED"
        assert backend.closed and list(tmp_path.iterdir()) == []
    finally:
        runtime.close()


def test_launch_failure_removes_temporary_profile(tmp_path):
    from tests.fakes.fake_browser import FakeBrowser
    backend = FakeBrowser()
    backend.fail_operation = "start"
    runtime, *_ = make_runtime(tmp_path, factory=lambda: backend)
    try:
        result = call(runtime, "launch")
        assert result.output["status"] == "FAILED"
        assert backend.closed and list(tmp_path.iterdir()) == []
    finally:
        runtime.close()


def test_cleanup_failure_does_not_leave_other_sessions_running(tmp_path):
    from tests.fakes.fake_browser import FakeBrowser
    from shuliu_runtime.browser.verification import BrowserError
    class LockedBrowser(FakeBrowser):
        locked = True
        def close(self):
            if self.locked:
                raise BrowserError("BROWSER_CLEANUP_FAILED")
            super().close()
    locked, other = LockedBrowser(), FakeBrowser()
    backends = iter([locked, other])
    runtime, *_ = make_runtime(tmp_path, factory=lambda: next(backends))
    call(runtime, "launch")
    call(runtime, "launch")
    try:
        with pytest.raises(BrowserError, match="BROWSER_CLEANUP_FAILED"):
            runtime.close()
        assert other.closed
        assert not Path(other.profiles[0]).exists()
        assert Path(locked.profiles[0]).exists()
    finally:
        locked.locked = False
        runtime.close()
    assert list(tmp_path.iterdir()) == []


def test_normalized_driver_disconnect_immediately_removes_profile(tmp_path):
    from tests.fakes.fake_browser import FakeBrowser
    from shuliu_runtime.browser.verification import BrowserError
    class DisconnectedBrowser(FakeBrowser):
        def request(self, operation, payload, **kwargs):
            if operation == "observe":
                raise BrowserError("BROWSER_CONNECTION_LOST")
            return super().request(operation, payload, **kwargs)
    backend = DisconnectedBrowser()
    runtime, *_ = make_runtime(tmp_path, factory=lambda: backend)
    try:
        session = call(runtime, "launch").output["session_id"]
        result = call(runtime, "observe", {"session_id": session})
        assert result.output["error_code"] == "BROWSER_CONNECTION_LOST"
        assert backend.closed
        assert list(tmp_path.iterdir()) == []
    finally:
        runtime.close()
