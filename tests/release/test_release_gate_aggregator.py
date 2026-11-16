"""Release gate aggregator semantics (Task 27 / ADR-027).

RED: the aggregator module does not exist yet; these tests fail until
tests/release/gate_aggregator.py implements the semantics below.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "gate-manifest.yaml"
POLICY = HERE / "evidence-policy.yaml"


def _load_yaml(path: Path):
    from gate_aggregator import load_yaml

    return load_yaml(path)


def _sha() -> str:
    return "a" * 40


def _evidence(gate_id: str, platform: str = "windows", **overrides) -> dict:
    evidence = {
        "gate_id": gate_id,
        "status": "PASS",
        "commit_sha": _sha(),
        "platform": platform,
        "runner": "ci",
        "recorded_at": "2026-11-16T00:00:00Z",
        "artifacts": ["logs/gate.log"],
    }
    if gate_id == "skill_sandbox_provider":
        evidence["enforced_controls"] = [
            "filesystem_scope",
            "network_scope",
            "secret_scope",
            "db_scope",
            "os_command_scope",
            "separate_process",
        ]
        evidence["adversarial_attacks"] = [
            "unauthorized_file",
            "unauthorized_network",
            "secret_read",
            "db_read",
            "os_command",
        ]
    evidence.update(overrides)
    return evidence


# ---------------------------------------------------------------- manifest


def test_the_manifest_is_the_single_required_list() -> None:
    manifest = _load_yaml(MANIFEST)
    ids = [gate["id"] for gate in manifest["required_gates"]]
    assert len(ids) == len(set(ids)), "gate ids must be unique"
    assert set(ids) == {
        # ADR-027 core nine
        "unit",
        "contract",
        "integration",
        "migration",
        "security",
        "ipc_handshake",
        "windows_build",
        "installer_smoke",
        "runtime_health",
        # capability gates
        "secure_storage_macos",
        "secure_storage_windows",
        "real_model_provider_connection",
        "model_provider_license_security",
        "model_usage_budget_persistence",
        "real_embedding_knowledge_retrieval",
        "knowledge_document_ingestion_pdf_docx_xlsx",
        "knowledge_server_rbac_trace_audit",
        "durable_idempotency",
        "process_crash_recovery",
        "skill_sandbox_provider",
        "skill_security_scan",
    }


def test_the_manifest_references_the_evidence_policy() -> None:
    manifest = _load_yaml(MANIFEST)
    policy = _load_yaml(POLICY)
    assert manifest["evidence_policy"] == "evidence-policy.yaml"
    assert "no_fake_engines" in [r["id"] for r in policy["provenance_rules"]]


# ---------------------------------------------------------------- aggregator


def _aggregate(results, *, release_sha: str | None = None):
    from gate_aggregator import aggregate

    return aggregate(MANIFEST, POLICY, results, release_sha=release_sha or _sha())


def test_all_required_gates_passing_yields_pass() -> None:
    manifest = _load_yaml(MANIFEST)
    results = []
    for gate in manifest["required_gates"]:
        platforms = gate.get("platforms") or [gate.get("platform") or "linux"]
        for platform in platforms:
            results.append(_evidence(gate["id"], platform))
    outcome = _aggregate(results)
    assert outcome["result"] == "PASS"
    assert outcome["blocked"] == []


def test_a_failed_gate_blocks_the_release() -> None:
    manifest = _load_yaml(MANIFEST)
    results = []
    for gate in manifest["required_gates"]:
        platforms = gate.get("platforms") or [gate.get("platform") or "linux"]
        for platform in platforms:
            status = "FAIL" if gate["id"] == "migration" else "PASS"
            results.append(_evidence(gate["id"], platform, status=status))
    outcome = _aggregate(results)
    assert outcome["result"] == "BLOCKED"
    assert [b["gate_id"] for b in outcome["blocked"]] == ["migration"]
    assert outcome["blocked"][0]["reason"] == "GATE_FAILED"


def test_a_not_run_gate_blocks_the_release() -> None:
    manifest = _load_yaml(MANIFEST)
    results = []
    for gate in manifest["required_gates"]:
        platforms = gate.get("platforms") or [gate.get("platform") or "linux"]
        for platform in platforms:
            status = "NOT_RUN" if gate["id"] == "skill_security_scan" else "PASS"
            results.append(_evidence(gate["id"], platform, status=status))
    outcome = _aggregate(results)
    assert outcome["result"] == "BLOCKED"
    assert outcome["blocked"][0]["reason"] == "GATE_NOT_RUN"


def test_a_missing_gate_blocks_the_release() -> None:
    manifest = _load_yaml(MANIFEST)
    results = []
    for gate in manifest["required_gates"]:
        if gate["id"] == "durable_idempotency":
            continue  # never reported at all
        platforms = gate.get("platforms") or [gate.get("platform") or "linux"]
        for platform in platforms:
            results.append(_evidence(gate["id"], platform))
    outcome = _aggregate(results)
    assert outcome["result"] == "BLOCKED"
    assert [b["gate_id"] for b in outcome["blocked"]] == ["durable_idempotency"]
    assert outcome["blocked"][0]["reason"] == "GATE_MISSING"


def test_missing_platform_evidence_blocks_a_platform_gate() -> None:
    manifest = _load_yaml(MANIFEST)
    results = []
    for gate in manifest["required_gates"]:
        platforms = gate.get("platforms") or [gate.get("platform") or "linux"]
        for platform in platforms:
            if gate["id"] == "skill_sandbox_provider" and platform == "macos":
                continue  # macOS sandbox evidence absent
            results.append(_evidence(gate["id"], platform))
    outcome = _aggregate(results)
    assert outcome["result"] == "BLOCKED"
    assert outcome["blocked"] == [
        {"gate_id": "skill_sandbox_provider", "reason": "PLATFORM_EVIDENCE_MISSING",
         "platform": "macos"}
    ]


def test_wrong_platform_evidence_is_not_accepted() -> None:
    manifest = _load_yaml(MANIFEST)
    results = []
    for gate in manifest["required_gates"]:
        platforms = gate.get("platforms") or [gate.get("platform") or "linux"]
        for platform in platforms:
            reported = "macos" if gate["id"] == "secure_storage_windows" else platform
            results.append(_evidence(gate["id"], reported))
    outcome = _aggregate(results)
    assert outcome["result"] == "BLOCKED"
    assert any(
        b["gate_id"] == "secure_storage_windows" and b["reason"] == "PLATFORM_MISMATCH"
        for b in outcome["blocked"]
    )


def test_fake_engine_evidence_is_refused_for_real_capability_gates() -> None:
    manifest = _load_yaml(MANIFEST)
    results = []
    for gate in manifest["required_gates"]:
        platforms = gate.get("platforms") or [gate.get("platform") or "linux"]
        for platform in platforms:
            if gate["id"] == "skill_security_scan":
                results.append(
                    _evidence(gate["id"], platform, implementation="FakeSkillScanner")
                )
            else:
                results.append(_evidence(gate["id"], platform))
    outcome = _aggregate(results)
    assert outcome["result"] == "BLOCKED"
    assert any(
        b["gate_id"] == "skill_security_scan" and b["reason"] == "FAKE_IMPLEMENTATION"
        for b in outcome["blocked"]
    )


def test_pid_only_sandbox_evidence_is_refused() -> None:
    manifest = _load_yaml(MANIFEST)
    results = []
    for gate in manifest["required_gates"]:
        platforms = gate.get("platforms") or [gate.get("platform") or "linux"]
        for platform in platforms:
            if gate["id"] == "skill_sandbox_provider":
                results.append(
                    _evidence(
                        gate["id"],
                        platform,
                        enforced_controls=["separate_process"],
                        adversarial_attacks=[],
                    )
                )
            else:
                results.append(_evidence(gate["id"], platform))
    outcome = _aggregate(results)
    assert outcome["result"] == "BLOCKED"
    assert any(
        b["gate_id"] == "skill_sandbox_provider" and b["reason"] == "INSUFFICIENT_CONTROLS"
        for b in outcome["blocked"]
    )


def test_stale_sha_evidence_fails_closed() -> None:
    manifest = _load_yaml(MANIFEST)
    release_sha = "b" * 40
    results = []
    for gate in manifest["required_gates"]:
        platforms = gate.get("platforms") or [gate.get("platform") or "linux"]
        for platform in platforms:
            results.append(_evidence(gate["id"], platform, commit_sha="c" * 40))
    outcome = _aggregate(results, release_sha=release_sha)
    assert outcome["result"] == "BLOCKED"
    # Every (gate, platform) evidence pair is stale, so every pair blocks.
    total_pairs = sum(
        len(g.get("platforms") or [g.get("platform") or "linux"])
        for g in manifest["required_gates"]
    )
    assert len(outcome["blocked"]) == total_pairs
    assert all(b["reason"] == "SHA_MISMATCH" for b in outcome["blocked"])


def test_credential_shaped_evidence_is_rejected() -> None:
    with pytest.raises(Exception) as error:
        _aggregate(
            [_evidence("unit", api_key="sk-live-1234567890")],
        )
    assert "secret" in str(error.value).lower()


def test_unknown_gate_ids_are_rejected() -> None:
    with pytest.raises(Exception) as error:
        _aggregate([_evidence("not_a_gate")])
    assert "unknown gate" in str(error.value).lower()


def test_results_are_serializable_gate_report() -> None:
    manifest = _load_yaml(MANIFEST)
    results = []
    for gate in manifest["required_gates"]:
        platforms = gate.get("platforms") or [gate.get("platform") or "linux"]
        for platform in platforms:
            results.append(_evidence(gate["id"], platform))
    outcome = _aggregate(results)
    # ADR-027: GitHub Actions publishes an explicit per-gate result.
    report = json.loads(json.dumps(outcome))
    assert report["result"] == "PASS"
    assert {g["gate_id"]: g["status"] for g in report["gates"]} == {
        g["id"]: "PASS" for g in manifest["required_gates"]
    }
