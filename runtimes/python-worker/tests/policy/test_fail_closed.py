import pytest

from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.rules import PolicyConfigurationError, PolicyRule


def _request(risk="LOW"):
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


def _allow_rule(**overrides):
    values = {
        "rule_id": "allow-task-read",
        "action": "task.read",
        "risk": "LOW",
        "decision": "ALLOW",
        "organization_id": "org-a",
        "permission": "foundation.task.read",
        "resource_type": "task",
        "classification": "INTERNAL",
        "privacy": "INTERNAL",
    }
    values.update(overrides)
    return PolicyRule(**values)


def test_missing_matching_rule_is_denied():
    result = PolicyEngine([], policy_version="policy-v1").evaluate(**_request())

    assert result.decision == "DENY"
    assert result.rule_id is None
    assert result.reason == "NO_MATCHING_RULE"


@pytest.mark.parametrize("risk", [None, "UNKNOWN", "critical", 4])
def test_unknown_or_malformed_risk_is_denied(risk):
    request = _request()
    request["action"]["risk"] = risk
    result = PolicyEngine([_allow_rule()], policy_version="policy-v1").evaluate(**request)

    assert result.decision == "DENY"
    assert result.reason == "UNKNOWN_RISK"


def test_missing_required_permission_is_denied():
    request = _request()
    request["subject"]["permissions"] = []

    result = PolicyEngine([_allow_rule()], policy_version="policy-v1").evaluate(**request)

    assert result.decision == "DENY"
    assert result.reason == "NO_MATCHING_RULE"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subject", {"subject_id": "user-1", "organization_id": "org-b", "permissions": ["foundation.task.read"]}),
        ("resource", {"organization_id": "org-b", "resource_type": "task", "classification": "INTERNAL"}),
        ("context", {"trace_id": "trace-1", "privacy": "INTERNAL", "organization_id": "org-b"}),
    ],
)
def test_cross_organization_requests_are_denied(field, value):
    request = _request()
    request[field] = value

    result = PolicyEngine([_allow_rule()], policy_version="policy-v1").evaluate(**request)

    assert result.decision == "DENY"
    assert result.reason == "ORGANIZATION_SCOPE_MISMATCH"


@pytest.mark.parametrize(
    ("rule_override", "request_field", "request_value"),
    [
        ({"classification": "RESTRICTED"}, "resource", {"organization_id": "org-a", "resource_type": "task", "classification": "INTERNAL"}),
        ({"privacy": "LOCAL_ONLY"}, "context", {"trace_id": "trace-1", "privacy": "INTERNAL", "organization_id": "org-a"}),
    ],
)
def test_classification_and_privacy_must_match_rule(
    rule_override, request_field, request_value
):
    request = _request()
    request[request_field] = request_value

    result = PolicyEngine(
        [_allow_rule(**rule_override)], policy_version="policy-v1"
    ).evaluate(**request)

    assert result.decision == "DENY"
    assert result.reason == "NO_MATCHING_RULE"


@pytest.mark.parametrize(
    "rule_overrides",
    [
        {"risk": "UNKNOWN"},
        {"decision": "EXECUTE"},
        {"rule_id": " "},
    ],
)
def test_invalid_policy_rules_are_rejected_before_evaluation(rule_overrides):
    with pytest.raises(PolicyConfigurationError):
        PolicyEngine(
            [_allow_rule(**rule_overrides)], policy_version="policy-v1"
        )


def test_duplicate_policy_rule_ids_are_rejected():
    with pytest.raises(PolicyConfigurationError):
        PolicyEngine(
            [_allow_rule(), _allow_rule()], policy_version="policy-v1"
        )


def test_missing_trace_context_is_denied():
    request = _request()
    request["context"].pop("trace_id")

    result = PolicyEngine([_allow_rule()], policy_version="policy-v1").evaluate(**request)

    assert result.decision == "DENY"
    assert result.reason == "INVALID_REQUEST"
