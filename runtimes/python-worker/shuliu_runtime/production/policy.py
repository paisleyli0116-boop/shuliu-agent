from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.rules import PolicyConfigurationError, PolicyRule

_BUNDLE_FIELDS = frozenset({"policy_version", "rules"})
_RULE_FIELDS = frozenset(
    {
        "rule_id",
        "action",
        "risk",
        "decision",
        "organization_id",
        "permission",
        "resource_type",
        "classification",
        "privacy",
    }
)


class PolicyBundleUnavailable(RuntimeError):
    """No Policy bundle is present, so trusted composition must not start."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PolicyBundleError(ValueError):
    """The Policy bundle exists but cannot be trusted as written."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def load_policy_bundle(path: str | Path) -> PolicyEngine:
    """Load the operator-supplied rule set into a fail-closed Policy engine.

    Unknown fields are rejected rather than ignored: a misspelled key must fail
    the load, because silently dropping it would leave the runtime looking
    configured while the intended rule never applied. An empty rule set is valid
    and denies everything, which is the safe default.
    """
    bundle_path = Path(path)
    try:
        raw = bundle_path.read_text(encoding="utf-8")
    except OSError as error:
        raise PolicyBundleUnavailable("POLICY_BUNDLE_UNAVAILABLE") from error
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PolicyBundleError("POLICY_BUNDLE_INVALID") from error
    if not isinstance(payload, Mapping):
        raise PolicyBundleError("POLICY_BUNDLE_INVALID")
    if set(payload) - _BUNDLE_FIELDS:
        raise PolicyBundleError("POLICY_BUNDLE_INVALID")
    policy_version = payload.get("policy_version")
    if not isinstance(policy_version, str) or not policy_version.strip():
        raise PolicyBundleError("POLICY_BUNDLE_INVALID")
    raw_rules = payload.get("rules")
    if not isinstance(raw_rules, list):
        raise PolicyBundleError("POLICY_BUNDLE_INVALID")

    rules: list[PolicyRule] = []
    for entry in raw_rules:
        if not isinstance(entry, Mapping) or set(entry) - _RULE_FIELDS:
            raise PolicyBundleError("POLICY_BUNDLE_INVALID")
        try:
            rules.append(
                PolicyRule(
                    rule_id=entry["rule_id"],  # type: ignore[arg-type]
                    action=entry["action"],  # type: ignore[arg-type]
                    risk=entry["risk"],  # type: ignore[arg-type]
                    decision=entry.get("decision"),  # type: ignore[arg-type]
                    organization_id=entry.get("organization_id"),  # type: ignore[arg-type]
                    permission=entry.get("permission"),  # type: ignore[arg-type]
                    resource_type=entry.get("resource_type"),  # type: ignore[arg-type]
                    classification=entry.get("classification"),  # type: ignore[arg-type]
                    privacy=entry.get("privacy"),  # type: ignore[arg-type]
                )
            )
        except KeyError as error:
            raise PolicyBundleError("POLICY_BUNDLE_INVALID") from error
    try:
        return PolicyEngine(rules, policy_version=policy_version.strip())
    except (PolicyConfigurationError, ValueError) as error:
        raise PolicyBundleError("POLICY_BUNDLE_INVALID") from error
