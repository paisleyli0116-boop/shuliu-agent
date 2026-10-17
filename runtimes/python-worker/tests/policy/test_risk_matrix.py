import pytest

from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.rules import PolicyRule


def _request(risk):
    return {
        "subject": {
            "subject_id": "user-1",
            "organization_id": "org-a",
            "permissions": ["foundation.task.read"],
        },
        "action": {"action_id": "task.read", "risk": risk},
        "resource": {
            "organization_id": "org-a",
            "resource_type": "task",
            "classification": "INTERNAL",
        },
        "context": {
            "trace_id": "trace-1",
            "privacy": "INTERNAL",
            "organization_id": "org-a",
        },
    }


@pytest.mark.parametrize(
    ("risk", "rule_decision", "expected"),
    [
        ("LOW", "ALLOW", "ALLOW"),
        ("MEDIUM", "ALLOW", "ALLOW"),
        ("HIGH", None, "REQUIRE_APPROVAL"),
        ("HIGH", "ALLOW", "ALLOW"),
        ("HIGH", "REQUIRE_APPROVAL", "REQUIRE_APPROVAL"),
        ("CRITICAL", None, "REQUIRE_APPROVAL"),
        ("CRITICAL", "ALLOW", "REQUIRE_APPROVAL"),
        ("CRITICAL", "DENY", "DENY"),
    ],
)
def test_risk_matrix_applies_explicit_rules_and_approval_defaults(
    risk, rule_decision, expected
):
    request = _request(risk)
    rule = PolicyRule(
        rule_id="rule-1",
        action="task.read",
        risk=risk,
        decision=rule_decision,
        organization_id="org-a",
        permission="foundation.task.read",
        resource_type="task",
        classification="INTERNAL",
        privacy="INTERNAL",
    )

    result = PolicyEngine([rule], policy_version="policy-v1").evaluate(**request)

    assert result.decision == expected
    assert result.risk == risk
    assert result.rule_id == "rule-1"
    assert result.policy_version == "policy-v1"
    assert result.trace_id == "trace-1"


@pytest.mark.parametrize("risk", ["LOW", "MEDIUM"])
def test_low_and_medium_require_an_explicit_allow_rule(risk):
    request = _request(risk)
    rule = PolicyRule(
        rule_id="rule-without-effect",
        action="task.read",
        risk=risk,
        organization_id="org-a",
        permission="foundation.task.read",
    )

    result = PolicyEngine([rule], policy_version="policy-v1").evaluate(**request)

    assert result.decision == "DENY"
