from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


SCHEMA_PATH = Path(__file__).parents[4] / "tests" / "release" / "model-provider-evidence.schema.json"


def _schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _real_evidence():
    return {
        "schema_version": "1.0.0",
        "evidence_type": "real_model_provider_connection",
        "execution_mode": "real_provider",
        "provider_id": "configured-provider",
        "model": "provider/model-name",
        "capability": "fast_chat",
        "status": "connected",
        "latency_ms": 123.4,
        "trace_id": "12345678-1234-4234-8234-123456789abc",
        "safe_error_code": None,
        "provider_response_received": True,
        "tested_at_utc": "2026-10-20T12:00:00Z",
        "license_review": {"status": "pending", "reference": ""},
        "data_processing_review": {"status": "pending", "reference": ""},
        "security_review": {"status": "pending", "reference": ""},
    }


def test_provider_evidence_schema_accepts_real_success_and_records_reviews():
    schema = _schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert validator.is_valid(_real_evidence())


def test_fake_provider_and_secret_material_cannot_be_real_connection_evidence():
    validator = Draft202012Validator(_schema(), format_checker=FormatChecker())
    fake = _real_evidence() | {"execution_mode": "fake_provider"}
    credentialed = _real_evidence() | {"api_key": "must-not-appear"}
    failed = _real_evidence() | {"status": "failed", "safe_error_code": "MODEL_PROVIDER_ERROR"}
    bad_timestamp = _real_evidence() | {"tested_at_utc": "not-a-timestamp"}
    secret_shaped_model = _real_evidence() | {"model": "sk-credential-marker-12345"}
    google_key_shaped_model = _real_evidence() | {
        "model": "AIzaSyD1234567890abcdefghijklmnoPQRSTUV"
    }
    jwt_shaped_model = _real_evidence() | {
        "model": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature"
    }
    oauth_token_shaped_model = _real_evidence() | {
        "model": "ya29.A0ARrdaM91Klq0f4tW5xY7zB8cD3eF6gH9"
    }
    opaque_token_shaped_model = _real_evidence() | {
        "model": "b8D4F2a9C1e7G6h3J5k0M2n8P4r1S9v5"
    }
    slack_token_shaped_model = _real_evidence() | {
        "model": "xoxb-123456789012-1234567890123-abcdefghijklmnopqrstuvwx"
    }
    custom_model = _real_evidence() | {
        "model": "provider/custom-model.version-one.preview-model"
    }
    google_key_shaped_provider_id = _real_evidence() | {
        "provider_id": "AIzaSyD1234567890abcdefghijklmnoPQRSTUV"
    }
    gitlab_token_shaped_model = _real_evidence() | {"model": "glpat-abcdefghijklmnopqrst"}
    gitlab_token_shaped_provider_id = _real_evidence() | {
        "provider_id": "glpat-abcdefghijklmnopqrst"
    }
    embedded_key_marker_model = _real_evidence() | {"model": "provider/api_key:credential-marker"}
    embedded_token_marker_provider_id = _real_evidence() | {
        "provider_id": "provider-token:credential-marker"
    }
    embedded_key_prefix_model = _real_evidence() | {"model": "provider/sk-credential-marker"}
    embedded_key_prefix_provider_id = _real_evidence() | {
        "provider_id": "provider-sk-credential-marker"
    }
    embedded_secret_ref_model = _real_evidence() | {"model": "provider/secret://os/key"}
    embedded_url_model = _real_evidence() | {"model": "provider/https://example.test/path"}
    assert not validator.is_valid(fake)
    assert not validator.is_valid(credentialed)
    assert not validator.is_valid(failed)
    assert not validator.is_valid(bad_timestamp)
    assert not validator.is_valid(secret_shaped_model)
    assert not validator.is_valid(google_key_shaped_model)
    assert not validator.is_valid(jwt_shaped_model)
    assert not validator.is_valid(oauth_token_shaped_model)
    assert not validator.is_valid(opaque_token_shaped_model)
    assert not validator.is_valid(slack_token_shaped_model)
    assert validator.is_valid(custom_model)
    assert not validator.is_valid(google_key_shaped_provider_id)
    assert not validator.is_valid(gitlab_token_shaped_model)
    assert not validator.is_valid(gitlab_token_shaped_provider_id)
    assert not validator.is_valid(embedded_key_marker_model)
    assert not validator.is_valid(embedded_token_marker_provider_id)
    assert not validator.is_valid(embedded_key_prefix_model)
    assert not validator.is_valid(embedded_key_prefix_provider_id)
    assert not validator.is_valid(embedded_secret_ref_model)
    assert not validator.is_valid(embedded_url_model)
