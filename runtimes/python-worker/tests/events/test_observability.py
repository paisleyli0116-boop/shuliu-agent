import pytest

from shuliu_runtime.events.observability import (
    create_log,
    record_metric,
    redact,
    validate_trace,
)


def test_trace_rejects_a_missing_parent_span() -> None:
    with pytest.raises(ValueError, match="parent"):
        validate_trace(
            [
                {
                    "span_id": "span-child",
                    "schema_version": 1,
                    "timestamp": "2026-10-17T08:00:00.000Z",
                    "organization_id": "org-01",
                    "trace_id": "trace-01",
                    "parent_span_id": "span-missing",
                    "started_at": "2026-10-17T08:00:00.000Z",
                }
            ]
        )


def test_trace_rejects_a_missing_ancestor_parent_span() -> None:
    with pytest.raises(ValueError, match="parent"):
        validate_trace(
            [
                {
                    "span_id": "span-child",
                    "schema_version": 1,
                    "timestamp": "2026-10-17T08:00:00.000Z",
                    "organization_id": "org-01",
                    "trace_id": "trace-01",
                    "parent_span_id": "span-root",
                    "started_at": "2026-10-17T08:00:00.000Z",
                },
                {
                    "span_id": "span-root",
                    "schema_version": 1,
                    "timestamp": "2026-10-17T08:00:00.000Z",
                    "organization_id": "org-01",
                    "trace_id": "trace-01",
                    "parent_span_id": "span-missing",
                    "started_at": "2026-10-17T08:00:00.000Z",
                },
            ]
        )


def test_metric_drops_user_content_labels() -> None:
    metric = record_metric(
        "tool.duration_ms",
        125,
        {"component": "browser", "prompt": "private user prompt"},
        {
            "schema_version": 1,
            "timestamp": "2026-10-17T08:00:00.000Z",
            "organization_id": "org-01",
            "trace_id": "trace-01",
        },
    )

    assert metric["labels"] == {"component": "browser"}
    assert "private user prompt" not in str(metric)


def test_metric_rejects_complex_labels() -> None:
    with pytest.raises(ValueError, match="label"):
        record_metric(
            "tool.duration_ms",
            125,
            {"component": {"text": "private user prompt"}},
            {
                "schema_version": 1,
                "timestamp": "2026-10-17T08:00:00.000Z",
                "organization_id": "org-01",
                "trace_id": "trace-01",
            },
        )


def test_log_has_structured_operational_fields() -> None:
    log = create_log(
        {
            "schema_version": 1,
            "timestamp": "2026-10-17T08:00:00.000Z",
            "organization_id": "org-01",
            "trace_id": "trace-01",
            "level": "warning",
            "component": "agent-core",
            "run_id": "run-01",
            "error_code": "TOOL_TIMEOUT",
            "retryable": True,
        }
    )

    assert log["trace_id"] == "trace-01"
    assert log["retryable"] is True
    assert "message" not in log


def test_redaction_removes_credentials_recursively() -> None:
    safe = redact(
        {
            "nested": {"api_key": "secret-key", "client_secret": "client-secret-value"},
            "headers": {"Cookie": "session=secret", "Authorization": "Bearer secret"},
            "secret_ref": "vault://credential-01",
            "trace_id": "trace-01",
        }
    )

    serialized = str(safe)
    assert "secret-key" not in serialized
    assert "session=secret" not in serialized
    assert "Bearer secret" not in serialized
    assert "client-secret-value" not in serialized
    assert "vault://credential-01" in serialized
    assert safe["trace_id"] == "trace-01"
