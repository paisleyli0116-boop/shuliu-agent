from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path

from shuliu_runtime.approval.service import ApprovalService
from shuliu_runtime.computer.ipc import create_computer_ipc_handlers
from shuliu_runtime.ipc.bootstrap import RuntimeBootstrapResult
from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.production.approvals import SQLiteApprovalRepository
from shuliu_runtime.production.catalog import (
    FileWorkflowCatalog,
    WorkflowCatalogError,
    WorkflowCatalogUnavailable,
)
from shuliu_runtime.production.desktop import (
    BROWSER_ORIGINS_ENVIRONMENT_KEY,
    DesktopRuntimeOutcome,
    compose_desktop_runtimes,
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


@dataclass(frozen=True)
class TrustedDependencies:
    """Everything the trusted composition needs, resolved exactly once.

    The Workflow broker and the desktop runtimes must share one identity,
    Policy engine and Approval service; resolving them twice could let the two
    paths disagree about who is acting or which Policy version applies.
    """

    identity_provider: EnvironmentIdentityProvider
    identity: object
    policy: object
    approvals: ApprovalService
    approval_ttl: timedelta
    principal: ProductionIdentity


@dataclass(frozen=True)
class ProductionRuntimeOutcome:
    """The Workflow composition and the desktop runtimes built alongside it."""

    workflow: CompositionOutcome
    desktop: DesktopRuntimeOutcome
    principal: ProductionIdentity | None = None
    policy: object | None = None


def compose_trusted_dependencies(
    database: SQLiteDatabase, environment: Mapping[str, str]
) -> tuple[TrustedDependencies | None, str | None]:
    """Resolve identity, Policy and Approval, or report the first blocker."""
    identity_provider = EnvironmentIdentityProvider(environment)
    try:
        identity = identity_provider.require()
    except ProductionIdentityUnavailable as error:
        return None, error.code

    approval_ttl = _approval_ttl(environment)
    if approval_ttl is None:
        return None, "APPROVAL_TTL_INVALID"

    bundle_path = _value(environment.get(POLICY_BUNDLE_ENVIRONMENT_KEY))
    if bundle_path is None:
        return None, "POLICY_BUNDLE_UNAVAILABLE"
    try:
        policy_engine = load_policy_bundle(bundle_path)
    except PolicyBundleUnavailable as error:
        return None, error.code
    except PolicyBundleError as error:
        return None, error.code

    approvals = ApprovalService(
        repository=SQLiteApprovalRepository(database),
        authorize_approver=_approver_authorizer(identity),
        approval_ttl=approval_ttl,
    )
    return (
        TrustedDependencies(
            identity_provider=identity_provider,
            identity=identity_provider.tool_broker_identity(),
            policy=policy_engine,
            approvals=approvals,
            approval_ttl=approval_ttl,
            principal=identity,
        ),
        None,
    )


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
    trusted, unavailable_code = compose_trusted_dependencies(database, environment)
    if trusted is None:
        return CompositionOutcome(None, unavailable_code)
    return _compose_workflow(trusted, environment, tuple(tool_providers))


def compose_production_runtime(
    database: SQLiteDatabase,
    environment: Mapping[str, str],
    *,
    data_directory: str | Path,
    tool_providers: Iterable[ToolRegistry] = (),
    browser_backend_factory=None,
    computer_backend=None,
    platform: str | None = None,
) -> ProductionRuntimeOutcome:
    """Compose Workflow, Browser and Computer from one trusted dependency set.

    Browser and Computer Tools are registered in the same broker the Workflow
    engine calls, so a Workflow step cannot reach a desktop executor that skips
    identity, Policy, Approval or durable idempotency.
    """
    trusted, unavailable_code = compose_trusted_dependencies(database, environment)
    if trusted is None:
        return ProductionRuntimeOutcome(
            CompositionOutcome(None, unavailable_code),
            DesktopRuntimeOutcome(None, None, unavailable_code, unavailable_code),
        )

    desktop = compose_desktop_runtimes(
        database=database,
        environment=environment,
        data_directory=data_directory,
        identity=trusted.identity,
        policy=trusted.policy,
        approvals=trusted.approvals,
        browser_backend_factory=browser_backend_factory,
        computer_backend=computer_backend,
        platform=platform,
    )
    workflow = _compose_workflow(
        trusted,
        environment,
        tuple(tool_providers) + desktop.tool_providers,
    )
    return ProductionRuntimeOutcome(workflow, desktop, trusted.principal, trusted.policy)


def _compose_workflow(
    trusted: TrustedDependencies,
    environment: Mapping[str, str],
    tool_providers: tuple[ToolRegistry, ...],
) -> CompositionOutcome:
    catalog_path = _value(environment.get(CATALOG_ENVIRONMENT_KEY))
    if catalog_path is None:
        return CompositionOutcome(None, "WORKFLOW_CATALOG_UNAVAILABLE")
    try:
        catalog = FileWorkflowCatalog(catalog_path).load()
    except WorkflowCatalogUnavailable as error:
        return CompositionOutcome(None, error.code)
    except WorkflowCatalogError as error:
        return CompositionOutcome(None, error.code)

    return CompositionOutcome(
        ProductionWorkflowDependencies(
            registry=compose_tool_registries(tool_providers),
            identity=trusted.identity,
            policy=trusted.policy,
            approvals=trusted.approvals,
            definition_resolver=catalog,
            run_context_provider=trusted.identity_provider.run_context,
        ),
        None,
        trusted.approval_ttl,
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


def attach_desktop_runtimes(
    bootstrap: RuntimeBootstrapResult, desktop: DesktopRuntimeOutcome
) -> RuntimeBootstrapResult:
    """Attach the desktop runtimes, keeping their unavailability codes visible."""
    return replace(
        bootstrap,
        browser_runtime=desktop.browser,
        computer_runtime=desktop.computer,
        browser_unavailable_code=desktop.browser_unavailable_code,
        computer_unavailable_code=desktop.computer_unavailable_code,
    )


def attach_production_runtime(
    bootstrap: RuntimeBootstrapResult, outcome: ProductionRuntimeOutcome
) -> RuntimeBootstrapResult:
    """Attach Workflow first, then the desktop runtimes built beside it."""
    attached = attach_desktop_runtimes(
        attach_workflow_runtime(
            bootstrap, outcome.workflow.dependencies, outcome.workflow.unavailable_code
        ),
        outcome.desktop,
    )
    handlers = {}
    if outcome.desktop.computer is not None and outcome.principal is not None:
        handlers = create_computer_ipc_handlers(
            outcome.desktop.computer,
            actor={"subject_id": outcome.principal.actor_id},
            organization_id=outcome.principal.organization_id,
            privacy=outcome.principal.privacy,
        )
    return replace(
        attached,
        computer_ipc_handlers=handlers,
        chat_principal=outcome.principal,
        chat_policy=outcome.policy,
    )


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
