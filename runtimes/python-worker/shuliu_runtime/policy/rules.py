from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from shuliu_runtime.policy.risk import RiskLevel, parse_risk_level


class PolicyConfigurationError(ValueError):
    """Raised when a policy rule set is invalid and cannot be evaluated safely."""


class PolicyOutcome(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


@dataclass(frozen=True)
class PolicyRule:
    rule_id: str
    action: str
    risk: RiskLevel | str
    decision: PolicyOutcome | str | None = None
    organization_id: str | None = None
    permission: str | None = None
    resource_type: str | None = None
    classification: str | None = None
    privacy: str | None = None


@dataclass(frozen=True)
class PolicyDecision:
    decision: PolicyOutcome
    risk: RiskLevel | None
    rule_id: str | None
    policy_version: str
    reason: str
    trace_id: str


def validate_policy_rules(rules: Iterable[PolicyRule]) -> tuple[PolicyRule, ...]:
    validated: list[PolicyRule] = []
    seen_ids: set[str] = set()

    for rule in rules:
        if not isinstance(rule, PolicyRule):
            raise PolicyConfigurationError("policy rule has an unsupported shape")
        if not _non_empty(rule.rule_id) or not _non_empty(rule.action):
            raise PolicyConfigurationError("policy rule ID and action are required")
        if rule.rule_id in seen_ids:
            raise PolicyConfigurationError("policy rule IDs must be unique")
        seen_ids.add(rule.rule_id)
        if parse_risk_level(rule.risk) is None:
            raise PolicyConfigurationError("policy rule risk is unknown")
        if rule.decision is not None and _parse_outcome(rule.decision) is None:
            raise PolicyConfigurationError("policy rule decision is unknown")
        for field_name in (
            "organization_id",
            "permission",
            "resource_type",
            "classification",
            "privacy",
        ):
            value = getattr(rule, field_name)
            if value is not None and not _non_empty(value):
                raise PolicyConfigurationError(f"policy rule {field_name} is invalid")
        validated.append(rule)

    return tuple(sorted(validated, key=lambda item: item.rule_id))


def _parse_outcome(value: object) -> PolicyOutcome | None:
    if isinstance(value, PolicyOutcome):
        return value
    if not isinstance(value, str):
        return None
    try:
        return PolicyOutcome(value)
    except ValueError:
        return None


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
