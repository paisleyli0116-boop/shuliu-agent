from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.rules import PolicyRule


def _rule(rule_id, decision):
    return PolicyRule(
        rule_id=rule_id,
        action="task.read",
        risk="LOW",
        decision=decision,
        organization_id="org-a",
        permission="foundation.task.read",
    )


def _request():
    return {
        "subject": {
            "subject_id": "user-1",
            "organization_id": "org-a",
            "permissions": ["foundation.task.read"],
        },
        "action": {"action_id": "task.read", "risk": "LOW"},
        "resource": {"organization_id": "org-a"},
        "context": {
            "trace_id": "trace-1",
            "privacy": "INTERNAL",
            "organization_id": "org-a",
        },
    }


def test_deny_wins_when_allow_and_deny_rules_both_match():
    engine = PolicyEngine(
        [_rule("allow-rule", "ALLOW"), _rule("deny-rule", "DENY")],
        policy_version="policy-v1",
    )

    result = engine.evaluate(**_request())

    assert result.decision == "DENY"
    assert result.rule_id == "deny-rule"
    assert result.reason == "EXPLICIT_DENY"


def test_require_approval_wins_over_allow_when_both_rules_match():
    engine = PolicyEngine(
        [_rule("allow-rule", "ALLOW"), _rule("approval-rule", "REQUIRE_APPROVAL")],
        policy_version="policy-v1",
    )

    result = engine.evaluate(**_request())

    assert result.decision == "REQUIRE_APPROVAL"
    assert result.rule_id == "approval-rule"
