"""Task 27-D: derive release gate results from GitHub Actions conclusions.

The ADR-027 release gate requires CI to report every gate as PASS / FAIL /
NOT_RUN.  This script maps the job / step conclusions of a workflow run onto
the authoritative gate manifest (tests/release/gate-manifest.yaml), then runs
the Task 27-A aggregator over the derived evidence and emits a release
report: ``PASS`` or ``BLOCKED`` with per-gate status.

Fail-closed rules preserved here:
- A missing step or skipped step is NOT_RUN, never PASS.
- A failed job marks every gate it maps to as FAIL.
- Results are only emitted for the platforms a gate requires; extra
  platform results for a single-platform gate would be rejected by the
  aggregator and must not be produced in the first place.
- Nothing is inferred about gates no CI step covers: they stay absent so the
  aggregator reports them NOT_RUN / GATE_MISSING.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate_aggregator import GateEvidenceError, aggregate, load_yaml  # noqa: E402

MANIFEST_PATH = Path(__file__).resolve().parent / "gate-manifest.yaml"

# Job-name substring -> evidence platform.
JOB_PLATFORM_HINTS = (
    ("ubuntu", "linux"),
    ("macos", "macos"),
    ("windows", "windows"),
)

STATUS_RANK = {"PASS": 0, "NOT_RUN": 1, "FAIL": 2}

# (job name, gate id, step names) triples.  A gate collects the worst status
# across all listed steps of the mapped job.  Job-level mappings (no steps)
# track the job conclusion directly.
GATE_STEP_MAP: list[dict[str, Any]] = [
    # --- Generic CI: ubuntu matrix job (platform linux) ---
    {"job": "Generic tests (ubuntu-latest)", "platform": "linux", "gate": "unit", "steps": [
        "Run Cloud API identity unit tests",
        "Run Cloud API route and authorization tests",
        "Run Cloud Worker lease and coordination unit tests",
        "Run Python Policy unit tests",
        "Run Python Approval unit tests",
        "Run Python Tool Broker and MCP unit tests",
        "Run Python Agent Core unit tests",
        "Run Python Workflow unit tests",
        "Run Python Model Gateway unit tests",
        "Run Python Worker protocol and registration unit tests",
        "Run Python Skill lifecycle and process isolation tests",
        "Run TypeScript unit and Browser Fake contract tests",
        "Run Desktop UI, IPC, and security unit tests",
    ]},
    {"job": "Generic tests (ubuntu-latest)", "platform": "linux", "gate": "security", "steps": [
        "Run repository security tests",
    ]},
    {"job": "Generic tests (ubuntu-latest)", "platform": "linux", "gate": "ipc_handshake", "steps": [
        "Run Electron ↔ Python IPC tests with real subprocesses",
    ]},
    {"job": "Generic tests (ubuntu-latest)", "platform": "linux", "gate": "contract", "steps": [
        "Run protocol and contract suites",
    ]},
    {"job": "Generic tests (ubuntu-latest)", "platform": "linux", "gate": "integration", "steps": [
        "Run generic integration tests without the dedicated real-browser suite",
    ]},
    {"job": "Generic tests (ubuntu-latest)", "platform": "linux", "gate": "migration", "steps": [
        "Run database migration checks",
    ]},
    {"job": "PostgreSQL migration (PG 17)", "platform": "linux", "gate": "migration", "steps": None},
    {"job": "PostgreSQL migration (PG 18)", "platform": "linux", "gate": "migration", "steps": None},
    # Durable persistence gates (Tasks 29 / 33): real PostgreSQL evidence.
    {"job": "PostgreSQL migration (PG 17)", "platform": "linux", "gate": "model_usage_budget_persistence", "steps": [
        "Run durable model usage and budget integration tests",
    ]},
    {"job": "PostgreSQL migration (PG 18)", "platform": "linux", "gate": "model_usage_budget_persistence", "steps": [
        "Run durable model usage and budget integration tests",
    ]},
    {"job": "PostgreSQL migration (PG 17)", "platform": "linux", "gate": "durable_idempotency", "steps": [
        "Run durable Tool idempotency restart and concurrency integration tests",
    ]},
    {"job": "PostgreSQL migration (PG 18)", "platform": "linux", "gate": "durable_idempotency", "steps": [
        "Run durable Tool idempotency restart and concurrency integration tests",
    ]},
    # Knowledge gates (Tasks 31 / 32): parser + ingestion and ACL/RBAC suites
    # run against real pgvector PostgreSQL in both migration jobs.
    {"job": "PostgreSQL migration (PG 17)", "platform": "linux", "gate": "knowledge_document_ingestion_pdf_docx_xlsx", "steps": [
        "Run PostgreSQL Knowledge unit and revocation tests",
    ]},
    {"job": "PostgreSQL migration (PG 18)", "platform": "linux", "gate": "knowledge_document_ingestion_pdf_docx_xlsx", "steps": [
        "Run PostgreSQL Knowledge unit and revocation tests",
    ]},
    {"job": "PostgreSQL migration (PG 17)", "platform": "linux", "gate": "knowledge_server_rbac_trace_audit", "steps": [
        "Run PostgreSQL Knowledge ACL integration tests",
    ]},
    {"job": "PostgreSQL migration (PG 18)", "platform": "linux", "gate": "knowledge_server_rbac_trace_audit", "steps": [
        "Run PostgreSQL Knowledge ACL integration tests",
    ]},
    # --- Generic CI: macOS matrix job (platform macos) ---
    {"job": "Generic tests (macos-latest)", "platform": "macos", "gate": "secure_storage_macos", "steps": [
        "Run Electron native safeStorage platform smoke (macOS Keychain)",
    ]},
    # --- Windows Validation workflow ---
    {"job": "Windows-only build, install, upgrade, rollback, and runtime validation", "platform": "windows", "gate": "windows_build", "steps": [
        "Build Electron Main, Preload, and Renderer",
        "Build standalone Windows Python Sidecar",
        "Build installable Windows NSIS packages for upgrade and rollback",
    ]},
    {"job": "Windows-only build, install, upgrade, rollback, and runtime validation", "platform": "windows", "gate": "installer_smoke", "steps": [
        "Install and smoke-test the Windows package, Safe Mode, and uninstall",
        "Verify upgrade, failed-runtime Safe Mode, rollback, and data preservation",
    ]},
    {"job": "Windows-only build, install, upgrade, rollback, and runtime validation", "platform": "windows", "gate": "runtime_health", "steps": [
        "Install and smoke-test the Windows package, Safe Mode, and uninstall",
    ]},
    {"job": "Windows-only build, install, upgrade, rollback, and runtime validation", "platform": "windows", "gate": "secure_storage_windows", "steps": [
        "Run Electron native safeStorage platform smoke (Windows DPAPI)",
    ]},
    # skill_sandbox_provider is intentionally NOT derived from step
    # conclusions: its PASS requires structured enforced_controls and
    # adversarial_attacks evidence (ADR-025/ADR-027) that only the dedicated
    # sandbox evidence suite (Task 35) can produce. Deriving it from a green
    # step would be over-claiming and is correctly rejected by the
    # aggregator as INSUFFICIENT_CONTROLS. The gate stays BLOCKED until
    # that evidence lands.
]

JOB_HARD_FAILURE = {"failure", "cancelled", "timed_out", "startup_failure"}


def _job_platform(job_name: str) -> str | None:
    for hint, platform in JOB_PLATFORM_HINTS:
        if hint in job_name.lower():
            return platform
    return None


def _step_conclusion(job: dict[str, Any], step_name: str) -> str:
    matched: list[str] = []
    for step in job.get("steps") or []:
        if step.get("name") == step_name:
            conclusion = step.get("conclusion")
            if conclusion == "success":
                matched.append("PASS")
            elif conclusion in (None, "skipped"):
                matched.append("NOT_RUN")
            else:
                matched.append("FAIL")
    if not matched:
        return "NOT_RUN"  # missing step never counts as PASS (fail closed)
    return max(matched, key=lambda s: STATUS_RANK[s])


def derive_gate_status(
    jobs: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    """Map run conclusions onto the manifest, one result per (gate, platform)."""
    required_platforms: dict[str, set[str]] = {}
    for gate in manifest["required_gates"]:
        platforms = gate.get("platforms") or [gate.get("platform") or "linux"]
        required_platforms[gate["id"]] = set(platforms)

    jobs_by_name = {job.get("name"): job for job in jobs}

    collected: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in GATE_STEP_MAP:
        platform = entry["platform"]
        if platform not in required_platforms.get(entry["gate"], set()):
            continue  # extra-platform evidence would poison a single-platform gate
        job = jobs_by_name.get(entry["job"])
        if job is None:
            statuses = ["NOT_RUN"]  # job absent from this run: not evidence of success
        else:
            conclusion = job.get("conclusion")
            if conclusion in JOB_HARD_FAILURE:
                statuses = ["FAIL"]
            elif conclusion != "success":
                statuses = ["NOT_RUN"]
            elif entry["steps"] is None:
                statuses = ["PASS"]  # job-level mapping: success conclusion suffices
            else:
                statuses = [_step_conclusion(job, step) for step in entry["steps"]]
        collected.setdefault((entry["gate"], platform), []).append(
            {"job": entry["job"], "statuses": statuses}
        )

    results: list[dict[str, Any]] = []
    for (gate_id, platform), provenance in sorted(collected.items()):
        statuses = [s for item in provenance for s in item["statuses"]]
        status = max(statuses, key=lambda s: STATUS_RANK[s])
        results.append(
            {
                "gate_id": gate_id,
                "status": status,
                "platform": platform,
                "artifacts": provenance,
            }
        )
    return results


def _fetch_jobs(repo: str, run_id: int) -> list[dict[str, Any]]:
    payload = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo}/actions/runs/{run_id}/jobs",
            "--paginate",
            "--jq",
            ".jobs",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(payload)


def _fetch_run_sha(repo: str, run_id: int) -> str:
    """Read the immutable head SHA recorded by GitHub for this workflow run."""
    return subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo}/actions/runs/{run_id}",
            "--jq",
            ".head_sha",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def build_report(
    jobs: list[dict[str, Any]],
    *,
    commit_sha: str,
    run_id: str,
    source_run_sha: str | None = None,
) -> dict[str, Any]:
    evidence_sha = source_run_sha or commit_sha
    manifest = load_yaml(MANIFEST_PATH)
    results = derive_gate_status(jobs, manifest)
    for result in results:
        result["commit_sha"] = evidence_sha
    report = aggregate(
        MANIFEST_PATH,
        MANIFEST_PATH.parent / "evidence-policy.yaml",
        results,
        release_sha=commit_sha,
    )
    report["source_run_id"] = run_id
    report["source_run_sha"] = evidence_sha
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Release gate report — result: **{report['result']}**",
        "",
        "| Gate | Platform | Status | Reason |",
        "| --- | --- | --- | --- |",
    ]
    for gate in report["gates"]:
        for entry in gate["results"]:
            lines.append(
                f"| {gate['gate_id']} | {entry['platform']} | {entry['status']} "
                f"| {entry.get('reason') or '-'} |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="owner/repo for gh api (omit with --jobs-json)")
    parser.add_argument("--run-id", help="GitHub Actions run id (omit with --jobs-json)")
    parser.add_argument("--jobs-json", help="path to a jobs JSON payload (for local/test use)")
    parser.add_argument("--commit", required=True, help="commit sha the results belong to")
    parser.add_argument("--out", required=True, help="path to write the JSON report")
    parser.add_argument("--summary", help="path to write a Markdown summary (optional)")
    args = parser.parse_args()

    if args.jobs_json:
        jobs = json.loads(Path(args.jobs_json).read_text(encoding="utf-8"))
        source_run_sha = args.commit
    else:
        if not args.repo or not args.run_id:
            parser.error("--repo and --run-id are required unless --jobs-json is given")
        source_run_sha = _fetch_run_sha(args.repo, int(args.run_id))
        jobs = _fetch_jobs(args.repo, int(args.run_id))

    try:
        report = build_report(
            jobs,
            commit_sha=args.commit,
            run_id=str(args.run_id or "local"),
            source_run_sha=source_run_sha,
        )
    except GateEvidenceError as error:
        print(f"GATE_EVIDENCE_ERROR: {error}", file=sys.stderr)
        return 2

    Path(args.out).write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if args.summary:
        Path(args.summary).write_text(render_markdown(report), encoding="utf-8")

    print(render_markdown(report))
    print(json.dumps({"result": report["result"]}, ensure_ascii=False))
    # PASS and BLOCKED are valid report outcomes (missing capability gates stay
    # BLOCKED until Tasks 28-36 land); an explicit FAIL blocks the job.
    if any(gate["status"] == "FAIL" for gate in report["gates"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
