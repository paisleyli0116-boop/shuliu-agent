"""Release gate aggregator for Foundation V0.1 (Task 27 / ADR-027).

Reads gate-manifest.yaml (the single authoritative required list) and
evidence-policy.yaml, then evaluates gate results into exactly one of:

- PASS    every required gate passed with platform-correct, provenance-clean
          evidence bound to the release SHA
- BLOCKED otherwise, with a machine-readable list of blocking reasons

Fake / InMemory / stub implementations, PID-only sandbox claims, stale SHAs,
wrong-platform evidence, and credential-shaped payloads all fail closed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent

VALID_STATUSES = ("PASS", "FAIL", "NOT_RUN")
VALID_PLATFORMS = ("linux", "macos", "windows")

# Evidence keys that must never carry a literal secret value. SecretRef-style
# references (strings starting with "secretref:" / "${secret:") are allowed.
CREDENTIAL_SHAPED_KEYS = (
    "password",
    "token",
    "api_key",
    "cookie",
    "authorization",
    "credential",
    "secret",
    "api-key",
    "access_token",
)

# Capability gates whose evidence must not be produced by a fake
# implementation. Values are the implementation declarations treated as fake.
FAKE_IMPLEMENTATION_MARKERS = ("fake", "mock", "stub", "inmemory", "in_memory")

# skill_sandbox_provider: controls beyond "a process id exists" that the
# evidence must claim before the gate can pass. A separate PID alone is not
# sandbox evidence (ADR-025).
REQUIRED_SANDBOX_CONTROLS = (
    "filesystem_scope",
    "network_scope",
    "secret_scope",
    "db_scope",
    "os_command_scope",
    "separate_process",
)
REQUIRED_SANDBOX_ATTACKS = (
    "unauthorized_file",
    "unauthorized_network",
    "secret_read",
    "db_read",
    "os_command",
)


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


class GateEvidenceError(ValueError):
    """Raised when evidence itself is malformed or unsafe, not merely failing."""


def _looks_like_secret(key: str, value: Any) -> bool:
    lowered = key.lower()
    if not any(marker in lowered for marker in CREDENTIAL_SHAPED_KEYS):
        return False
    if not isinstance(value, str):
        return False
    if value.startswith(("secretref:", "${secret:", "vault:", "env:")):
        return False  # reference form is allowed
    return True


def _validate_evidence(result: dict[str, Any], valid_gate_ids: set[str]) -> None:
    if not isinstance(result, dict):
        raise GateEvidenceError("each gate result must be an object")
    gate_id = result.get("gate_id")
    if gate_id not in valid_gate_ids:
        raise GateEvidenceError(f"unknown gate id: {gate_id!r}")
    if result.get("status") not in VALID_STATUSES:
        raise GateEvidenceError(f"gate {gate_id}: invalid status {result.get('status')!r}")
    platform = result.get("platform")
    if platform not in VALID_PLATFORMS:
        raise GateEvidenceError(f"gate {gate_id}: invalid platform {platform!r}")
    sha = result.get("commit_sha")
    if not isinstance(sha, str) or len(sha) != 40 or not all(c in "0123456789abcdef" for c in sha.lower()):
        raise GateEvidenceError(f"gate {gate_id}: commit_sha must be a full 40-char hex SHA")
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise GateEvidenceError(f"gate {gate_id}: artifacts must be a non-empty list")
    for key, value in result.items():
        if _looks_like_secret(key, value):
            raise GateEvidenceError(
                f"gate {gate_id}: evidence key {key!r} carries a secret-shaped value; "
                "use a SecretRef reference instead"
            )


def _declares_fake_implementation(result: dict[str, Any]) -> bool:
    for key in ("implementation", "engine", "adapter", "backend", "store"):
        value = result.get(key)
        if isinstance(value, str) and any(
            marker in value.lower() for marker in FAKE_IMPLEMENTATION_MARKERS
        ):
            return True
    return False


def _required_platforms(gate: dict[str, Any]) -> list[str]:
    platforms = gate.get("platforms") or [gate.get("platform") or "linux"]
    return list(platforms)


def aggregate(
    manifest_path: Path,
    policy_path: Path,
    results: list[dict[str, Any]],
    *,
    release_sha: str,
) -> dict[str, Any]:
    """Evaluate gate results against the manifest and evidence policy."""
    manifest = load_yaml(manifest_path)
    policy = load_yaml(policy_path)

    gates = {gate["id"]: gate for gate in manifest["required_gates"]}
    valid_gate_ids = set(gates)

    # Malformed / unsafe evidence aborts the whole evaluation (fail closed)
    # rather than silently ignoring the entry.
    for result in results:
        _validate_evidence(result, valid_gate_ids)

    release_sha = release_sha.lower()

    # Index results by gate. Duplicate results for the same (gate, platform)
    # keep the worst outcome; contradictory statuses for the same pair are
    # rejected outright rather than averaged.
    by_gate: dict[str, dict[str, dict[str, Any]]] = {}
    for result in results:
        platform_results = by_gate.setdefault(result["gate_id"], {})
        existing = platform_results.get(result["platform"])
        if existing is None:
            platform_results[result["platform"]] = result
            continue
        rank = {"PASS": 0, "NOT_RUN": 1, "FAIL": 2}
        if rank[result["status"]] > rank[existing["status"]]:
            platform_results[result["platform"]] = result
        elif rank[result["status"]] < rank[existing["status"]]:
            raise GateEvidenceError(
                f"gate {result['gate_id']}: contradictory {result['platform']} results "
                f"({existing['status']} vs {result['status']})"
            )

    report_gates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for gate_id, gate in gates.items():
        gate_platforms = _required_platforms(gate)
        gate_results = []
        for platform in gate_platforms:
            # A single-platform gate must have exactly one result; it is a
            # platform mismatch when that result claims another platform.
            # A platform-listed gate needs one result per listed platform.
            platform_results = by_gate.get(gate_id, {})
            if len(gate_platforms) == 1:
                if not platform_results:
                    result = None
                elif len(platform_results) == 1:
                    result = next(iter(platform_results.values()))
                else:
                    raise GateEvidenceError(
                        f"gate {gate_id}: a single-platform gate must have one "
                        f"result, got {sorted(platform_results)}"
                    )
            else:
                result = platform_results.get(platform)

            if result is None:
                reason = (
                    "GATE_MISSING"
                    if not platform_results
                    else "PLATFORM_EVIDENCE_MISSING"
                )
                blocked.append(
                    {"gate_id": gate_id, "reason": reason, "platform": platform}
                )
                gate_results.append({"platform": platform, "status": "NOT_RUN", "reason": reason})
                continue

            status = result["status"]
            reason = None
            if result["commit_sha"].lower() != release_sha:
                status = "FAIL"
                reason = "SHA_MISMATCH"
            elif policy.get("provenance_rules") and result["status"] == "PASS":
                if _declares_fake_implementation(result):
                    status = "FAIL"
                    reason = "FAKE_IMPLEMENTATION"
                elif gate_id == "skill_sandbox_provider":
                    controls = set(result.get("enforced_controls") or [])
                    attacks = set(result.get("adversarial_attacks") or [])
                    if not set(REQUIRED_SANDBOX_CONTROLS).issubset(controls) or not (
                        set(REQUIRED_SANDBOX_ATTACKS).issubset(attacks)
                    ):
                        status = "FAIL"
                        reason = "INSUFFICIENT_CONTROLS"
                elif gate.get("platform") and result["platform"] != gate["platform"]:
                    status = "FAIL"
                    reason = "PLATFORM_MISMATCH"

            if reason is None and status == "FAIL":
                reason = "GATE_FAILED"
            elif reason is None and status == "NOT_RUN":
                reason = "GATE_NOT_RUN"

            if status == "PASS" and reason is None:
                blocked_entry = None
            else:
                blocked_entry = {
                    "gate_id": gate_id,
                    "reason": reason,
                    "platform": platform,
                }
                if reason == "PLATFORM_MISMATCH":
                    blocked_entry["reported_platform"] = result["platform"]
                blocked.append(blocked_entry)

            gate_results.append({"platform": platform, "status": status, "reason": reason})

        report_gates.append(
            {"gate_id": gate_id, "status": "PASS" if not any(
                gr["status"] != "PASS" for gr in gate_results
            ) else "FAIL" if any(gr["status"] == "FAIL" for gr in gate_results) else "NOT_RUN",
             "results": gate_results}
        )

    return {
        "result": "PASS" if not blocked else "BLOCKED",
        "release": manifest["release"],
        "manifest_version": manifest["manifest_version"],
        "commit_sha": release_sha,
        "gates": report_gates,
        "blocked": blocked,
    }
