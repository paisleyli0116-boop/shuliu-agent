import pytest

from shuliu_runtime.browser.diagnostics import failure_diagnostic, validate_driver_diagnostic, validate_progress


@pytest.mark.parametrize(
    ("message", "failure_kind"),
    [
        ("No usable sandbox! Failed to move to new namespace", "CHROME_SANDBOX_UNAVAILABLE"),
        ("Failed to launch browser process", "CHROME_PROCESS_EXITED"),
        ("Executable doesn't exist at /private/user/profile/chrome", "CHROME_EXECUTABLE_UNAVAILABLE"),
        ("Permission denied: /home/runner/profile", "PERMISSION_DENIED"),
        ("Connection refused", "CONNECTION_REFUSED"),
        ("Unexpected upstream failure", "UPSTREAM_ERROR"),
    ],
)
def test_failure_diagnostic_classifies_only_allowlisted_browser_failure_kinds(message, failure_kind):
    diagnostic = failure_diagnostic("start_browser", RuntimeError(message))

    assert diagnostic == {
        "stage": "start_browser",
        "exception_type": "RuntimeError",
        "failure_kind": failure_kind,
    }


def test_failure_diagnostic_never_returns_raw_message_or_secret_values():
    diagnostic = failure_diagnostic(
        "navigate_goto",
        RuntimeError("token=browser-secret cookie=fixture-cookie https://user:pass@example.test/path /tmp/profile"),
    )

    serialized = str(diagnostic)
    assert "browser-secret" not in serialized
    assert "fixture-cookie" not in serialized
    assert "user:pass" not in serialized
    assert "/tmp/profile" not in serialized
    assert diagnostic["failure_kind"] == "UPSTREAM_ERROR"


def test_failure_diagnostic_rejects_unrecognized_stage_names():
    diagnostic = failure_diagnostic("https://page.example/private", RuntimeError("failed"))

    assert diagnostic["stage"] == "unknown"
    assert "page.example" not in str(diagnostic)


def test_driver_diagnostic_validation_rejects_arbitrary_or_secret_fields():
    assert validate_driver_diagnostic({
        "stage": "start_browser",
        "exception_type": "RuntimeError",
        "failure_kind": "UPSTREAM_ERROR",
        "message": "cookie=driver-secret",
    }) is None
    assert validate_driver_diagnostic({
        "stage": ["https://page.example/private"],
        "exception_type": "RuntimeError",
        "failure_kind": "UPSTREAM_ERROR",
    }) is None
    assert validate_driver_diagnostic({
        "stage": "start_browser",
        "exception_type": "RuntimeError",
        "failure_kind": ["secret"],
    }) is None


def test_driver_diagnostic_validation_accepts_only_fixed_safe_fields():
    expected = {"stage": "navigate_goto", "exception_type": "TimeoutError", "failure_kind": "OPERATION_TIMEOUT"}

    assert validate_driver_diagnostic(expected) == expected


def test_progress_validation_accepts_only_known_fixed_stage():
    assert validate_progress({"stage": "navigate_goto"}) == "navigate_goto"
    assert validate_progress({"stage": ["private"]}) is None
    assert validate_progress({"stage": "https://page.example/private"}) is None
    assert validate_progress({"stage": "navigate_goto", "url": "https://private.example"}) is None
