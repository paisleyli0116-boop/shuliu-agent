from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import timedelta

from shuliu_runtime.approval.service import ApprovalService
from shuliu_runtime.ipc.bootstrap import RuntimeBootstrapResult
from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.production.approvals import SQLiteApprovalRepository
from shuliu_runtime.production.catalog import (
    FileWorkflowCatalog,
    WorkflowCatalogError,
    WorkflowCatalogUnavailable,
)
from shuliu_runtime.production.identity import (
    EnvironmentIdentityProvider,
    ProductionIdentity,
    ProductionIdentityUnavailable,
)
from shuliu_runtime.production.policy import (
    PolicyBundleError,
    PolicyBundleUnavailable,
    load_policy_bundle,
)
from shuliu_runtime.production.tools import compose_tool_registries
from shuliu_runtime.tools.registry import ToolRegistry
from shuliu_runtime.workflow.composition import (
    ProductionWorkflowDependencies,
    ProductionWorkflowRuntime,
)

POLICY_BUNDLE_ENVIRONMENT_KEY = "SHULIU_POLICY_BUNDLE"
CATALOG_ENVIRONMENT_KEY = "SHULIU_WORKFLOW_CATALOG"
APPROVAL_TTL_ENVIRONMENT_KEY = "SHULIU_APPROVAL_TTL_SECONDS"

_DEFAULT_APPROVAL_TTL_SECONDS = 900
_APPROVAL_DECIDE_PERMISSION = "foundation.approval.decide"


@dataclass(frozen=True)
class CompositionOutcome:
    """Either the trusted dependencies, or the reason they cannot be trusted."""

    dependencies: ProductionWorkflowDependencies | None
    unavailable_code: str | None
    approval_ttl: timedelta | None = None


def compose_production_dependencies(
    database: SQLiteDatabase,
    environment: Mapping[str, str],
    *,
    tool_providers: Iterable[ToolRegistry] = (),
) -> CompositionOutcome:
    """Build the trusted Workflow composition from deployed configuration.

    Every input is deployment configuration read from the process environment,
    which only the Desktop Main process can set. Nothing here is derived from a
    Renderer payload, and no missing value is defaulted into a working identity.
    """
    identity_provider = EnvironmentIdentityProvider(environment)
    try:
        identity = identity_provider.require()
    except ProductionIdentityUnavailable as error:
        return CompositionOutcome(None, error.code)

    approval_ttl = _approval_ttl(environment)
    if approval_ttl is None:
        return CompositionOutcome(None, "APPROVAL_TTL_INVALID")

    bundle_path = _value(environment.get(POLICY_BUNDLE_ENVIRONMENT_KEY))
    if bundle_path is None:
        return CompositionOutcome(None, "POLICY_BUNDLE_UNAVAILABLE")
    try:
        policy_engine = load_policy_bundle(bundle_path)
    except PolicyBundleUnavailable as error:
        return CompositionOutcome(None, error.code)
    except PolicyBundleError as error:
        return CompositionOutcome(None, error.code)

    catalog_path = _value(environment.get(CATALOG_ENVIRONMENT_KEY))
    if catalog_path is None:
        return CompositionOutcome(None, "WORKFLOW_CATALOG_UNAVAILABLE")
    try:
        catalog = FileWorkflowCatalog(catalog_path).load()
    except WorkflowCatalogUnavailable as error:
        return CompositionOutcome(None, error.code)
    except WorkflowCatalogError as error:
        return CompositionOutcome(None, error.code)

    approvals = ApprovalService(
        repository=SQLiteApprovalRepository(database),
        authorize_approver=_approver_authorizer(identity),
        approval_ttl=approval_ttl,
    )
    return CompositionOutcome(
        ProductionWorkflowDependencies(
            registry=compose_tool_registries(tool_providers),
            identity=identity_provider.tool_broker_identity(),
            policy=policy_engine,
            approvals=approvals,
            definition_resolver=catalog,
            run_context_provider=identity_provider.run_context,
        ),
        None,
        approval_ttl,
    )


def attach_workflow_runtime(
    bootstrap: RuntimeBootstrapResult,
    dependencies: ProductionWorkflowDependencies | None,
    unavailable_code: str | None = None,
) -> RuntimeBootstrapResult:
    """Attach the Workflow runtime to an already bootstrapped database."""
    if dependencies is None:
        return replace(bootstrap, workflow_unavailable_code=unavailable_code)
    runtime = ProductionWorkflowRuntime.build(
        database=bootstrap.database,
        repository=bootstrap.workflow_repository,
        dependencies=dependencies,
    )
    return replace(bootstrap, workflow_runtime=runtime)


def _approver_authorizer(identity: ProductionIdentity):
    """Only the trusted identity may decide, and only with the decide permission."""

    def authorize(
        *,
        organization_id: str,
        approver_id: str,
        permission: str,
        resource: Mapping[str, object],
    ) -> bool:
        return (
            organization_id == identity.organization_id
            and approver_id == identity.actor_id
            and permission == _APPROVAL_DECIDE_PERMISSION
            and permission in identity.permissions
        )

    return authorize


def _approval_ttl(environment: Mapping[str, str]) -> timedelta | None:
    raw = _value(environment.get(APPROVAL_TTL_ENVIRONMENT_KEY))
    if raw is None:
        return timedelta(seconds=_DEFAULT_APPROVAL_TTL_SECONDS)
    try:
        seconds = int(raw)
    except ValueError:
        return None
    if seconds <= 0 or seconds > 86_400:
        return None
    return timedelta(seconds=seconds)


def _value(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip()
    return trimmed or None
