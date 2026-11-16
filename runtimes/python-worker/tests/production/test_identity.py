from __future__ import annotations

import pytest

from shuliu_runtime.production.identity import (
    EnvironmentIdentityProvider,
    ProductionIdentity,
    ProductionIdentityUnavailable,
)

ORGANIZATION_ID = "org-foundation"
ACTOR_ID = "user-owner"
PERMISSIONS = "foundation.task.create,foundation.task.assign_worker"


def environment(**overrides: str) -> dict[str, str]:
    values = {
        "SHULIU_ACTOR_ID": ACTOR_ID,
        "SHULIU_ORGANIZATION_ID": ORGANIZATION_ID,
        "SHULIU_ACTOR_PERMISSIONS": PERMISSIONS,
        "SHULIU_CONTEXT_PRIVACY": "internal",
    }
    values.update(overrides)
    return {key: value for key, value in values.items() if value is not None}


def provider(**overrides: str) -> EnvironmentIdentityProvider:
    return EnvironmentIdentityProvider(
        environment(**overrides),
        trace_id_factory=lambda: "trace-fixed",
    )


def test_complete_environment_yields_the_declared_identity() -> None:
    resolution = provider().resolve()

    assert resolution.error_code is None
    assert resolution.identity == ProductionIdentity(
        actor_id=ACTOR_ID,
        organization_id=ORGANIZATION_ID,
        permissions=(
            "foundation.task.create",
            "foundation.task.assign_worker",
        ),
        privacy="internal",
    )


@pytest.mark.parametrize(
    "missing",
    ["SHULIU_ACTOR_ID", "SHULIU_ORGANIZATION_ID", "SHULIU_CONTEXT_PRIVACY"],
)
def test_absent_identity_variable_keeps_the_runtime_unconfigured(missing: str) -> None:
    # 缺少身份时只能不可用，不能回退到 "local" 或任何默认主体。
    resolution = provider(**{missing: None}).resolve()

    assert resolution.identity is None
    assert resolution.error_code == "RUNTIME_IDENTITY_UNAVAILABLE"


@pytest.mark.parametrize(
    "missing",
    ["SHULIU_ACTOR_ID", "SHULIU_ORGANIZATION_ID", "SHULIU_CONTEXT_PRIVACY"],
)
def test_blank_identity_variable_is_not_treated_as_configured(missing: str) -> None:
    resolution = provider(**{missing: "   "}).resolve()

    assert resolution.identity is None
    assert resolution.error_code == "RUNTIME_IDENTITY_UNAVAILABLE"


def test_absent_permission_grant_is_not_treated_as_unlimited_access() -> None:
    # 没有权限声明必须 fail-closed，不能被解读成"拥有全部权限"。
    resolution = provider(SHULIU_ACTOR_PERMISSIONS=None).resolve()

    assert resolution.identity is None
    assert resolution.error_code == "RUNTIME_IDENTITY_UNAVAILABLE"


def test_empty_permission_grant_is_rejected() -> None:
    resolution = provider(SHULIU_ACTOR_PERMISSIONS=" , ,").resolve()

    assert resolution.identity is None
    assert resolution.error_code == "RUNTIME_IDENTITY_UNAVAILABLE"


def test_duplicate_permission_grant_is_rejected() -> None:
    resolution = provider(
        SHULIU_ACTOR_PERMISSIONS="foundation.task.create,foundation.task.create"
    ).resolve()

    assert resolution.identity is None
    assert resolution.error_code == "RUNTIME_IDENTITY_INVALID"


def test_permission_grant_of_an_unexpected_shape_is_rejected() -> None:
    resolution = provider(
        SHULIU_ACTOR_PERMISSIONS="foundation.task.create,../escape"
    ).resolve()

    assert resolution.identity is None
    assert resolution.error_code == "RUNTIME_IDENTITY_INVALID"


def test_identity_variables_are_trimmed_before_use() -> None:
    resolution = provider(
        SHULIU_ACTOR_ID="  user-owner  ",
        SHULIU_ORGANIZATION_ID="  org-foundation  ",
        SHULIU_ACTOR_PERMISSIONS=" foundation.task.create ",
    ).resolve()

    assert resolution.identity is not None
    assert resolution.identity.actor_id == ACTOR_ID
    assert resolution.identity.organization_id == ORGANIZATION_ID
    assert resolution.identity.permissions == ("foundation.task.create",)


def test_trusted_actor_resolution_returns_the_principal_for_the_same_actor() -> None:
    identity_resolver = provider().tool_broker_identity()

    principal = identity_resolver.resolve(
        {
            "subject_id": ACTOR_ID,
            "organization_id": ORGANIZATION_ID,
            "permissions": ["foundation.task.create"],
        }
    )

    assert principal is not None
    assert principal.subject_id == ACTOR_ID
    assert principal.organization_id == ORGANIZATION_ID
    # 权限始终取自可信身份，不从请求体继承。
    assert principal.permissions == (
        "foundation.task.create",
        "foundation.task.assign_worker",
    )


def test_trusted_actor_resolution_rejects_a_substituted_subject() -> None:
    identity_resolver = provider().tool_broker_identity()

    assert (
        identity_resolver.resolve(
            {"subject_id": "user-attacker", "organization_id": ORGANIZATION_ID}
        )
        is None
    )


def test_trusted_actor_resolution_rejects_a_substituted_organization() -> None:
    identity_resolver = provider().tool_broker_identity()

    assert (
        identity_resolver.resolve(
            {"subject_id": ACTOR_ID, "organization_id": "org-other"}
        )
        is None
    )


def test_trusted_actor_resolution_rejects_an_absent_actor() -> None:
    identity_resolver = provider().tool_broker_identity()

    assert identity_resolver.resolve({}) is None


def test_run_context_is_built_only_from_the_trusted_identity() -> None:
    context = provider().run_context()

    assert context["organization_id"] == ORGANIZATION_ID
    assert context["trace_id"] == "trace-fixed"
    assert context["privacy"] == "internal"
    assert context["privacy"] == "internal"
    assert context["actor"] == {
        "subject_id": ACTOR_ID,
        "organization_id": ORGANIZATION_ID,
        "permissions": [
            "foundation.task.create",
            "foundation.task.assign_worker",
        ],
    }


def test_run_context_refuses_to_invent_an_identity() -> None:
    with pytest.raises(ProductionIdentityUnavailable) as error:
        provider(SHULIU_ORGANIZATION_ID=None).run_context()

    assert error.value.code == "RUNTIME_IDENTITY_UNAVAILABLE"
