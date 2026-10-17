from __future__ import annotations

from collections.abc import Iterable, Mapping

from shuliu_runtime.policy.risk import RiskLevel, parse_risk_level
from shuliu_runtime.policy.rules import (
    PolicyDecision,
    PolicyOutcome,
    PolicyRule,
    validate_policy_rules,
)


class PolicyEngine:
    """Evaluate explicit, organization-scoped rules with fail-closed defaults."""

    def __init__(self, rules: Iterable[PolicyRule], *, policy_version: str) -> None:
        if not isinstance(policy_version, str) or not policy_version.strip():
            raise ValueError("policy_version is required")
        self._rules = validate_policy_rules(rules)
        self._policy_version = policy_version

    def evaluate(
        self,
        *,
        subject: Mapping[str, object],
        action: Mapping[str, object],
        resource: Mapping[str, object] | None,
        context: Mapping[str, object],
    ) -> PolicyDecision:
        trace_id = _text(context.get("trace_id")) if isinstance(context, Mapping) else ""
        risk = parse_risk_level(action.get("risk")) if isinstance(action, Mapping) else None
        if risk is None:
            return self._decision(
                PolicyOutcome.DENY, None, None, "UNKNOWN_RISK", trace_id
            )

        if not _valid_request(subject, action, resource, context):
            return self._decision(
                PolicyOutcome.DENY, risk, None, "INVALID_REQUEST", trace_id
            )

        organization_id = subject["organization_id"]
        if (
            context["organization_id"] != organization_id
            or (
                resource is not None
                and "organization_id" in resource
                and resource["organization_id"] != organization_id
            )
            or (
                "organization_id" in action
                and action["organization_id"] != organization_id
            )
        ):
            return self._decision(
                PolicyOutcome.DENY,
                risk,
                None,
                "ORGANIZATION_SCOPE_MISMATCH",
                trace_id,
            )

        permissions = frozenset(subject.get("permissions", ()))
        matching_rules = tuple(
            rule
            for rule in self._rules
            if _matches(
                rule,
                risk=risk,
                organization_id=organization_id,
                permissions=permissions,
                action=action,
                resource=resource,
                context=context,
            )
        )
        if not matching_rules:
            return self._decision(
                PolicyOutcome.DENY, risk, None, "NO_MATCHING_RULE", trace_id
            )

        deny_rule = next(
            (
                rule
                for rule in matching_rules
                if _outcome(rule) is PolicyOutcome.DENY
            ),
            None,
        )
        if deny_rule is not None:
            return self._decision(
                PolicyOutcome.DENY,
                risk,
                deny_rule.rule_id,
                "EXPLICIT_DENY",
                trace_id,
            )

        approval_rule = next(
            (
                rule
                for rule in matching_rules
                if _outcome(rule) is PolicyOutcome.REQUIRE_APPROVAL
            ),
            None,
        )
        if risk is RiskLevel.CRITICAL:
            return self._decision(
                PolicyOutcome.REQUIRE_APPROVAL,
                risk,
                approval_rule.rule_id if approval_rule else matching_rules[0].rule_id,
                "CRITICAL_APPROVAL_REQUIRED",
                trace_id,
            )
        if approval_rule is not None:
            return self._decision(
                PolicyOutcome.REQUIRE_APPROVAL,
                risk,
                approval_rule.rule_id,
                "APPROVAL_REQUIRED",
                trace_id,
            )
        if risk is RiskLevel.HIGH:
            default_approval_rule = next(
                (rule for rule in matching_rules if rule.decision is None), None
            )
            if default_approval_rule is not None:
                return self._decision(
                    PolicyOutcome.REQUIRE_APPROVAL,
                    risk,
                    default_approval_rule.rule_id,
                    "HIGH_RISK_APPROVAL_REQUIRED",
                    trace_id,
                )

        allow_rule = next(
            (rule for rule in matching_rules if _outcome(rule) is PolicyOutcome.ALLOW),
            None,
        )
        if allow_rule is not None:
            return self._decision(
                PolicyOutcome.ALLOW,
                risk,
                allow_rule.rule_id,
                "EXPLICIT_ALLOW",
                trace_id,
            )

        return self._decision(
            PolicyOutcome.DENY,
            risk,
            matching_rules[0].rule_id,
            "NO_EXPLICIT_EFFECT",
            trace_id,
        )

    def _decision(
        self,
        decision: PolicyOutcome,
        risk: RiskLevel | None,
        rule_id: str | None,
        reason: str,
        trace_id: str,
    ) -> PolicyDecision:
        return PolicyDecision(
            decision=decision,
            risk=risk,
            rule_id=rule_id,
            policy_version=self._policy_version,
            reason=reason,
            trace_id=trace_id,
        )


def _valid_request(
    subject: object,
    action: object,
    resource: object,
    context: object,
) -> bool:
    if not isinstance(subject, Mapping) or not isinstance(action, Mapping):
        return False
    if resource is not None and not isinstance(resource, Mapping):
        return False
    if not isinstance(context, Mapping):
        return False
    if not _text(subject.get("subject_id")) or not _text(subject.get("organization_id")):
        return False
    if not _text(action.get("action_id")):
        return False
    if not _text(context.get("trace_id")) or not _text(context.get("privacy")):
        return False
    if not _text(context.get("organization_id")):
        return False

    permissions = subject.get("permissions", ())
    if not isinstance(permissions, (list, tuple, set, frozenset)):
        return False
    return all(_text(permission) for permission in permissions)


def _matches(
    rule: PolicyRule,
    *,
    risk: RiskLevel,
    organization_id: object,
    permissions: frozenset[object],
    action: Mapping[str, object],
    resource: Mapping[str, object] | None,
    context: Mapping[str, object],
) -> bool:
    if rule.action != action.get("action_id") or parse_risk_level(rule.risk) is not risk:
        return False
    if rule.organization_id is not None and rule.organization_id != organization_id:
        return False
    if rule.permission is not None and rule.permission not in permissions:
        return False
    if rule.resource_type is not None and (
        resource is None or resource.get("resource_type") != rule.resource_type
    ):
        return False
    if rule.classification is not None and (
        resource is None or resource.get("classification") != rule.classification
    ):
        return False
    if rule.privacy is not None and context.get("privacy") != rule.privacy:
        return False
    return True


def _outcome(rule: PolicyRule) -> PolicyOutcome | None:
    if rule.decision is None:
        return None
    return PolicyOutcome(rule.decision)


def _text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value
