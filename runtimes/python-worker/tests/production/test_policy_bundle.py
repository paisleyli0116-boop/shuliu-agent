from __future__ import annotations

import json

import pytest

from shuliu_runtime.policy.rules import PolicyOutcome
from shuliu_runtime.production.policy import (
    PolicyBundleError,
    PolicyBundleUnavailable,
    load_policy_bundle,
)

ORGANIZATION_ID = "org-foundation"


def write_bundle(path, payload: object):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def bundle(rules: list[dict[str, object]] | None = None, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "policy_version": "foundation-v0.1",
        "rules": rules
        if rules is not None
        else [
            {
                "rule_id": "allow-task-create",
                "action": "foundation.task.create",
                "risk": "LOW",
                "decision": "ALLOW",
                "permission": "foundation.task.create",
            }
        ],
    }
    payload.update(overrides)
    return payload


def evaluate(engine, *, action_id: str = "foundation.task.create", risk: str = "LOW"):
    return engine.evaluate(
        subject={
            "subject_id": "user-owner",
            "organization_id": ORGANIZATION_ID,
            "permissions": ["foundation.task.create"],
        },
        action={"action_id": action_id, "risk": risk},
        resource=None,
        context={
            "organization_id": ORGANIZATION_ID,
            "trace_id": "trace-1",
            "privacy": "internal",
        },
    )


def test_a_valid_bundle_allows_the_action_it_names(tmp_path) -> None:
    engine = load_policy_bundle(write_bundle(tmp_path / "policy.json", bundle()))

    decision = evaluate(engine)

    assert decision.decision is PolicyOutcome.ALLOW
    assert decision.rule_id == "allow-task-create"
    assert decision.policy_version == "foundation-v0.1"


def test_an_absent_bundle_is_reported_as_unavailable(tmp_path) -> None:
    with pytest.raises(PolicyBundleUnavailable) as error:
        load_policy_bundle(tmp_path / "missing.json")

    assert error.value.code == "POLICY_BUNDLE_UNAVAILABLE"


def test_a_malformed_bundle_is_rejected(tmp_path) -> None:
    path = tmp_path / "policy.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(PolicyBundleError) as error:
        load_policy_bundle(path)

    assert error.value.code == "POLICY_BUNDLE_INVALID"


def test_a_bundle_without_a_version_is_rejected(tmp_path) -> None:
    payload = bundle()
    payload.pop("policy_version")

    with pytest.raises(PolicyBundleError) as error:
        load_policy_bundle(write_bundle(tmp_path / "policy.json", payload))

    assert error.value.code == "POLICY_BUNDLE_INVALID"


def test_a_bundle_without_a_rule_list_is_rejected(tmp_path) -> None:
    payload = bundle()
    payload.pop("rules")

    with pytest.raises(PolicyBundleError) as error:
        load_policy_bundle(write_bundle(tmp_path / "policy.json", payload))

    assert error.value.code == "POLICY_BUNDLE_INVALID"


def test_an_unknown_top_level_field_is_rejected(tmp_path) -> None:
    # 拼错的字段不能被静默忽略，否则一次笔误会变成"规则没生效但看起来已配置"。
    with pytest.raises(PolicyBundleError) as error:
        load_policy_bundle(
            write_bundle(tmp_path / "policy.json", bundle(rule=[]))
        )

    assert error.value.code == "POLICY_BUNDLE_INVALID"


def test_an_unknown_rule_field_is_rejected(tmp_path) -> None:
    rules = [
        {
            "rule_id": "allow-task-create",
            "action": "foundation.task.create",
            "risk": "LOW",
            "decision": "ALLOW",
            "permisions": "foundation.task.create",
        }
    ]

    with pytest.raises(PolicyBundleError) as error:
        load_policy_bundle(write_bundle(tmp_path / "policy.json", bundle(rules)))

    assert error.value.code == "POLICY_BUNDLE_INVALID"


def test_an_empty_rule_set_denies_every_action(tmp_path) -> None:
    engine = load_policy_bundle(write_bundle(tmp_path / "policy.json", bundle([])))

    decision = evaluate(engine)

    assert decision.decision is PolicyOutcome.DENY
    assert decision.reason == "NO_MATCHING_RULE"


def test_a_rule_for_another_risk_level_does_not_apply(tmp_path) -> None:
    engine = load_policy_bundle(write_bundle(tmp_path / "policy.json", bundle()))

    decision = evaluate(engine, risk="HIGH")

    assert decision.decision is PolicyOutcome.DENY


def test_a_rule_can_require_approval(tmp_path) -> None:
    rules = [
        {
            "rule_id": "publish-requires-approval",
            "action": "tool.publish",
            "risk": "HIGH",
            "decision": "REQUIRE_APPROVAL",
        }
    ]
    engine = load_policy_bundle(write_bundle(tmp_path / "policy.json", bundle(rules)))

    decision = evaluate(engine, action_id="tool.publish", risk="HIGH")

    assert decision.decision is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.rule_id == "publish-requires-approval"


def test_a_bundle_that_is_not_an_object_is_rejected(tmp_path) -> None:
    with pytest.raises(PolicyBundleError) as error:
        load_policy_bundle(write_bundle(tmp_path / "policy.json", []))

    assert error.value.code == "POLICY_BUNDLE_INVALID"
