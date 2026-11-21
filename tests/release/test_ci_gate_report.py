"""Task 27-D: fail-closed tests for CI gate result derivation."""

import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate_aggregator import GateEvidenceError, aggregate, load_yaml  # noqa: E402

ci_gate_report = importlib.import_module("ci_gate_report")

MANIFEST = load_yaml(ci_gate_report.MANIFEST_PATH)


def _job(name, conclusion, steps=None):
    return {"name": name, "conclusion": conclusion, "steps": steps or []}


def _step(name, conclusion):
    return {"name": name, "conclusion": conclusion}


def _shaped_sha(i: int = 0) -> str:
    return f"abcdef{i:02d}" + "0123456789abcdef0123456789abcdef"


def _jobs_all_pass(include_windows: bool = False):
    """Build success jobs from the mapping table itself so tests stay in sync."""
    jobs: dict[str, dict] = {}
    for entry in ci_gate_report.GATE_STEP_MAP:
        if entry["job"].startswith("Windows-only") and not include_windows:
            continue
        job = jobs.setdefault(
            entry["job"],
            {"name": entry["job"], "conclusion": "success", "steps": []},
        )
        if entry["steps"]:
            for name in entry["steps"]:
                if all(step["name"] != name for step in job["steps"]):
                    job["steps"].append(_step(name, "success"))
    return list(jobs.values())


def _derive(jobs):
    return {(r["gate_id"], r["platform"]): r["status"] for r in ci_gate_report.derive_gate_status(jobs, MANIFEST)}


def test_passing_linux_and_macos_jobs_produce_pass_results():
    derived = _derive(_jobs_all_pass())
    for gate in ("unit", "security", "ipc_handshake", "contract", "integration", "migration"):
        assert derived[(gate, "linux")] == "PASS", gate
    assert derived[("secure_storage_macos", "macos")] == "PASS"


def test_failed_step_yields_fail_even_when_job_succeeded():
    jobs = _jobs_all_pass()
    for job in jobs:
        if job["name"] == "Generic tests (ubuntu-latest)":
            job["steps"].append(_step("Run repository security tests", "failure"))
    derived = _derive(jobs)
    assert derived[("security", "linux")] == "FAIL"


def test_failed_job_marks_all_mapped_gates_fail():
    jobs = _jobs_all_pass()
    for job in jobs:
        if job["name"] == "PostgreSQL migration (PG 17)":
            job["conclusion"] = "failure"
    derived = _derive(jobs)
    assert derived[("migration", "linux")] == "FAIL"


def test_missing_step_is_not_run_not_pass():
    jobs = _jobs_all_pass()
    for job in jobs:
        if job["name"] == "Generic tests (ubuntu-latest)":
            job["steps"] = [s for s in job["steps"] if s["name"] != "Run protocol and contract suites"]
    derived = _derive(jobs)
    assert derived[("contract", "linux")] == "NOT_RUN"


def test_absent_job_is_not_run_not_pass():
    jobs = [j for j in _jobs_all_pass() if j["name"] != "PostgreSQL migration (PG 17)"]
    derived = _derive(jobs)
    assert derived[("migration", "linux")] == "NOT_RUN"


def test_skipped_step_is_not_run():
    jobs = _jobs_all_pass()
    for job in jobs:
        if job["name"] == "Generic tests (macos-latest)":
            job["steps"] = [_step("Run Electron native safeStorage platform smoke (macOS Keychain)", "skipped")]
    derived = _derive(jobs)
    assert derived[("secure_storage_macos", "macos")] == "NOT_RUN"


def test_extra_platform_results_for_single_platform_gate_are_not_emitted():
    derived = _derive(_jobs_all_pass())
    # unit only requires linux; macos runs the same suites and must not be recorded
    assert ("unit", "macos") not in derived
    assert ("contract", "macos") not in derived


def test_worst_status_wins_within_one_gate():
    jobs = _jobs_all_pass()
    for job in jobs:
        if job["name"] == "Generic tests (ubuntu-latest)":
            job["steps"].append(_step("Run database migration checks", "skipped"))
    derived = _derive(jobs)
    assert derived[("migration", "linux")] == "NOT_RUN"


def test_unmapped_capability_gates_stay_blocked():
    """Gates with no CI coverage (Tasks 28-36) must remain NOT_RUN, never PASS."""
    manifest = ci_gate_report.load_yaml(ci_gate_report.MANIFEST_PATH)
    results = ci_gate_report.derive_gate_status(_jobs_all_pass(), manifest)
    for result in results:
        result["commit_sha"] = _shaped_sha()
    report = aggregate(
        ci_gate_report.MANIFEST_PATH,
        ci_gate_report.MANIFEST_PATH.parent / "evidence-policy.yaml",
        results,
        release_sha=_shaped_sha(),
    )
    assert report["result"] == "BLOCKED"
    blocked_ids = {entry["gate_id"] for entry in report["blocked"]}
    for gate in ("real_model_provider_connection", "durable_idempotency", "skill_security_scan"):
        assert gate in blocked_ids, gate
    covered = {entry["gate_id"] for entry in report["blocked"]}
    for gate in ("unit", "security", "ipc_handshake", "contract", "integration", "migration"):
        assert gate not in covered, gate


def test_sha_mismatch_fails_the_release():
    manifest = ci_gate_report.load_yaml(ci_gate_report.MANIFEST_PATH)
    results = ci_gate_report.derive_gate_status(_jobs_all_pass(), manifest)
    for result in results:
        result["commit_sha"] = "deadbeef" + "0" * 32
    report = aggregate(
        ci_gate_report.MANIFEST_PATH,
        ci_gate_report.MANIFEST_PATH.parent / "evidence-policy.yaml",
        results,
        release_sha=_shaped_sha(),
    )
    assert report["result"] == "BLOCKED"
    assert any(entry["reason"] == "SHA_MISMATCH" for entry in report["blocked"])


def test_render_markdown_lists_every_gate_platform():
    manifest = ci_gate_report.load_yaml(ci_gate_report.MANIFEST_PATH)
    results = ci_gate_report.derive_gate_status(_jobs_all_pass(), manifest)
    for result in results:
        result["commit_sha"] = _shaped_sha()
    report = aggregate(
        ci_gate_report.MANIFEST_PATH,
        ci_gate_report.MANIFEST_PATH.parent / "evidence-policy.yaml",
        results,
        release_sha=_shaped_sha(),
    )
    text = ci_gate_report.render_markdown(report)
    assert "| unit | linux | PASS" in text
    assert "| secure_storage_macos | macos | PASS" in text
    assert "GATE_MISSING" in text  # capability gates without any CI coverage
