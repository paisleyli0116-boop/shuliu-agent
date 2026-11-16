"""Release gate evaluation for `skill_security_scan`.

The shared release gate manifest is assembled by Task 27 and intentionally
not modified here.  This test pins the gate semantics now, using the scanner
policy as the source of truth, so Task 27 cannot assemble a manifest that
accepts missing, not-run, or fake-only scan evidence.

A fake scanner is never valid Release evidence: any evidence whose engine is
not in the approved list of `tests/release/skill-scanner-policy.yaml` is
BLOCKED, as is evidence with missing fields, a non-clean verdict, or a
digest that does not match the released package.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

POLICY_PATH = Path(__file__).parent / "skill-scanner-policy.yaml"


@dataclass(frozen=True)
class GateDecision:
    gate: str
    status: str  # PASS | BLOCKED
    reason: str = ""


def evaluate_skill_security_gate(
    evidence: dict[str, object] | None, *, package_digest: str, policy: dict[str, object]
) -> GateDecision:
    gate_policy = policy["skill_security_scan"]
    if not isinstance(gate_policy, dict) or not gate_policy.get("required"):
        return GateDecision("skill_security_scan", "BLOCKED", "gate is not configured as required")
    if evidence is None:
        return GateDecision("skill_security_scan", "BLOCKED", "scan evidence is missing")
    if not isinstance(evidence, dict):
        return GateDecision("skill_security_scan", "BLOCKED", "scan evidence is malformed")

    required = gate_policy["required_evidence"]
    missing = [field for field in required if not evidence.get(field)]
    if missing:
        return GateDecision(
            "skill_security_scan",
            "BLOCKED",
            f"scan evidence is missing required fields: {', '.join(missing)}",
        )

    approved = {
        entry["engine"]
        for entry in gate_policy["approved_engines"]
        if isinstance(entry, dict) and entry.get("engine")
    }
    if evidence.get("engine") not in approved:
        return GateDecision(
            "skill_security_scan",
            "BLOCKED",
            f"engine {evidence.get('engine')!r} is not an approved real scanner engine",
        )

    if evidence.get("verdict") != "CLEAN":
        return GateDecision(
            "skill_security_scan",
            "BLOCKED",
            f"verdict {evidence.get('verdict')!r} does not allow staging or activation",
        )

    if evidence.get("package_digest") != package_digest:
        return GateDecision(
            "skill_security_scan",
            "BLOCKED",
            "scan evidence digest does not match the released package digest",
        )

    return GateDecision("skill_security_scan", "PASS")


@pytest.fixture(scope="module")
def policy() -> dict[str, object]:
    with POLICY_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _evidence(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "package_digest": "sha256:" + "a" * 64,
        "engine": "clamav",
        "engine_version": "1.5.4",
        "ruleset_version": "28173",
        "scanned_at": datetime.now(UTC).isoformat(),
        "verdict": "CLEAN",
    }
    for key, value in overrides.items():
        if value is None:
            base.pop(key, None)
        else:
            base[key] = value
    return base


def test_the_policy_pins_clamav_as_the_reviewed_engine(policy) -> None:
    engines = policy["skill_security_scan"]["approved_engines"]
    assert [entry["engine"] for entry in engines] == ["clamav"]
    review = engines[0]["review"]
    assert review["maintenance_status"] == "active"
    assert engines[0]["license"] == "GPL-2.0-or-later"
    assert review["offline_capable"] is True
    assert review["ruleset_source"]


def test_real_engine_clean_evidence_passes(policy) -> None:
    decision = evaluate_skill_security_gate(
        _evidence(), package_digest="sha256:" + "a" * 64, policy=policy
    )
    assert decision.status == "PASS"


def test_missing_evidence_is_blocked(policy) -> None:
    decision = evaluate_skill_security_gate(None, package_digest="sha256:" + "a" * 64, policy=policy)
    assert decision.status == "BLOCKED"


def test_not_run_evidence_is_blocked(policy) -> None:
    decision = evaluate_skill_security_gate(
        _evidence(verdict="NOT_RUN"), package_digest="sha256:" + "a" * 64, policy=policy
    )
    assert decision.status == "BLOCKED"


def test_fake_only_evidence_is_blocked(policy) -> None:
    decision = evaluate_skill_security_gate(
        _evidence(engine="fake", engine_version="0.0.0", ruleset_version="fake-ruleset"),
        package_digest="sha256:" + "a" * 64,
        policy=policy,
    )
    assert decision.status == "BLOCKED"
    assert "not an approved" in decision.reason


def test_detected_verdict_is_blocked(policy) -> None:
    decision = evaluate_skill_security_gate(
        _evidence(verdict="DETECTED"), package_digest="sha256:" + "a" * 64, policy=policy
    )
    assert decision.status == "BLOCKED"


def test_error_verdict_is_blocked(policy) -> None:
    decision = evaluate_skill_security_gate(
        _evidence(verdict="ERROR", reason_code="SCAN_TIMEOUT"),
        package_digest="sha256:" + "a" * 64,
        policy=policy,
    )
    assert decision.status == "BLOCKED"


def test_digest_mismatch_is_blocked(policy) -> None:
    decision = evaluate_skill_security_gate(
        _evidence(package_digest="sha256:" + "b" * 64),
        package_digest="sha256:" + "a" * 64,
        policy=policy,
    )
    assert decision.status == "BLOCKED"


def test_incomplete_evidence_fields_are_blocked(policy) -> None:
    decision = evaluate_skill_security_gate(
        _evidence(ruleset_version=None), package_digest="sha256:" + "a" * 64, policy=policy
    )
    assert decision.status == "BLOCKED"
    assert "ruleset_version" in decision.reason
