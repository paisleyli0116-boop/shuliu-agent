from __future__ import annotations

import json

import pytest

from shuliu_runtime.production.evidence import (
    EvidenceRecordInvalid,
    EvidenceStoreUnavailable,
    FileEvidenceStore,
)

RECORD = {
    "schema_version": 1,
    "trace_id": "trace-evidence-1",
    "organization_id": "org-foundation",
    "operation": "navigate",
    "phase": "BEFORE",
    "observation": {"url": "https://example.test/", "text": "Fixture page"},
}


def test_an_evidence_record_is_persisted_and_referenced(tmp_path) -> None:
    store = FileEvidenceStore(tmp_path / "evidence")

    reference = store(RECORD)

    assert reference.startswith("evidence:")
    stored = list((tmp_path / "evidence").glob("*.json"))
    assert len(stored) == 1
    assert json.loads(stored[0].read_text(encoding="utf-8")) == RECORD


def test_the_same_record_yields_a_stable_reference_without_duplication(tmp_path) -> None:
    store = FileEvidenceStore(tmp_path / "evidence")

    first = store(RECORD)
    second = store(RECORD)

    assert first == second
    assert len(list((tmp_path / "evidence").glob("*.json"))) == 1


def test_a_different_record_gets_its_own_reference(tmp_path) -> None:
    store = FileEvidenceStore(tmp_path / "evidence")

    assert store(RECORD) != store({**RECORD, "trace_id": "trace-evidence-2"})


def test_an_oversized_record_is_refused(tmp_path) -> None:
    store = FileEvidenceStore(tmp_path / "evidence", max_record_bytes=512)

    with pytest.raises(EvidenceRecordInvalid) as error:
        store({**RECORD, "observation": {"text": "x" * 4096}})

    assert error.value.code == "EVIDENCE_RECORD_TOO_LARGE"
    assert list((tmp_path / "evidence").glob("*.json")) == []


@pytest.mark.parametrize(
    "key", ["password", "api_key", "access_token", "client_secret", "authorization"]
)
def test_a_credential_shaped_key_is_refused(tmp_path, key: str) -> None:
    store = FileEvidenceStore(tmp_path / "evidence")

    with pytest.raises(EvidenceRecordInvalid) as error:
        store({**RECORD, "detail": {key: "must-not-be-persisted"}})

    assert error.value.code == "EVIDENCE_RECORD_SENSITIVE"
    assert list((tmp_path / "evidence").glob("*.json")) == []


def test_a_non_serialisable_record_is_refused(tmp_path) -> None:
    store = FileEvidenceStore(tmp_path / "evidence")

    with pytest.raises(EvidenceRecordInvalid) as error:
        store({**RECORD, "detail": object()})

    assert error.value.code == "EVIDENCE_RECORD_INVALID"


def test_an_unusable_store_fails_closed(tmp_path) -> None:
    blocker = tmp_path / "evidence"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(EvidenceStoreUnavailable) as error:
        FileEvidenceStore(blocker)

    assert error.value.code == "EVIDENCE_STORE_UNAVAILABLE"
