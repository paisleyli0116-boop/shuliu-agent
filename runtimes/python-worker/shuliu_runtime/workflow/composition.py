from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Event
from typing import Any

from shuliu_runtime.events.models import EventRecord, event_to_dict
from shuliu_runtime.persistence.outbox import OutboxRepository
from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.persistence.sqlite_idempotency import SQLiteIdempotencyStore
from shuliu_runtime.tools.broker import (
    ApprovalConsumer,
    IdentityResolver,
    PolicyEvaluator,
    ToolBroker,
)
from shuliu_runtime.tools.registry import ToolRegistry
from shuliu_runtime.workflow.definition import WorkflowDefinition
from shuliu_runtime.workflow.engine import WorkflowEngine, WorkflowRepository


class WorkflowCompositionError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ProductionWorkflowDependencies:
    """Trusted application dependencies; none may be sourced from Renderer payloads."""

    registry: ToolRegistry
    identity: IdentityResolver
    policy: PolicyEvaluator
    approvals: ApprovalConsumer | None
    definition_resolver: Callable[[str, str], WorkflowDefinition | None]
    run_context_provider: Callable[[], Mapping[str, object]]


class ProductionWorkflowRuntime:
    """Durable WorkflowEngine -> ToolBroker composition for a bootstrapped Sidecar."""

    def __init__(
        self,
        *,
        engine: WorkflowEngine,
        broker: ToolBroker,
        dependencies: ProductionWorkflowDependencies,
    ) -> None:
        self.engine = engine
        self._broker = broker
        self._dependencies = dependencies

    @property
    def tool_broker(self) -> ToolBroker:
        """The single gated path a Worker uses to execute Cloud-assigned Tools."""
        return self._broker

    @classmethod
    def build(
        cls,
        *,
        database: SQLiteDatabase,
        repository: WorkflowRepository,
        dependencies: ProductionWorkflowDependencies,
    ) -> ProductionWorkflowRuntime:
        if not isinstance(database, SQLiteDatabase):
            raise TypeError("production Workflow runtime requires SQLiteDatabase")
        if not callable(getattr(repository, "create", None)) or not callable(
            getattr(repository, "compare_and_set", None)
        ):
            raise TypeError("durable Workflow repository is required")
        if not isinstance(dependencies.registry, ToolRegistry):
            raise TypeError("trusted ToolRegistry is required")
        if not callable(getattr(dependencies.identity, "resolve", None)):
            raise TypeError("trusted identity resolver is required")
        if not callable(getattr(dependencies.policy, "evaluate", None)):
            raise TypeError("trusted Policy evaluator is required")
        if not callable(dependencies.definition_resolver) or not callable(
            dependencies.run_context_provider
        ):
            raise TypeError("trusted Workflow providers are required")

        outbox = OutboxRepository(database)

        def emit_tool_event(event: EventRecord) -> None:
            outbox.enqueue(
                event.event_id,
                f"workflow-tool-event:{event.event_id}",
                event_to_dict(event),
            )

        broker = ToolBroker(
            registry=dependencies.registry,
            identity=dependencies.identity,
            policy=dependencies.policy,
            approvals=dependencies.approvals,
            event_sink=emit_tool_event,
            idempotency_store=SQLiteIdempotencyStore(database),
        )
        return cls(
            engine=WorkflowEngine(repository=repository, tool_broker=broker),
            broker=broker,
            dependencies=dependencies,
        )

    def create_ipc_handlers(self) -> dict[str, Callable[[Mapping[str, Any], Event], object]]:
        def start_workflow(
            payload: Mapping[str, Any], cancellation: Event
        ) -> dict[str, object]:
            if (
                not isinstance(payload, Mapping)
                or set(payload) != {"workflow_id", "workflow_version_id", "input"}
                or not _non_empty(payload.get("workflow_id"))
                or not _non_empty(payload.get("workflow_version_id"))
                or not isinstance(payload.get("input"), Mapping)
            ):
                raise WorkflowCompositionError("WORKFLOW_REQUEST_INVALID")
            if cancellation.is_set():
                raise WorkflowCompositionError("WORKFLOW_REQUEST_CANCELLED")
            try:
                definition = self._dependencies.definition_resolver(
                    payload["workflow_id"], payload["workflow_version_id"]
                )
            except Exception:
                raise WorkflowCompositionError(
                    "WORKFLOW_DEFINITION_UNAVAILABLE"
                ) from None
            if not isinstance(definition, WorkflowDefinition) or (
                definition.workflow_id != payload["workflow_id"]
                or definition.workflow_version_id != payload["workflow_version_id"]
            ):
                raise WorkflowCompositionError("WORKFLOW_DEFINITION_UNAVAILABLE")
            try:
                context = self._dependencies.run_context_provider()
            except Exception:
                raise WorkflowCompositionError(
                    "WORKFLOW_CONTEXT_UNAVAILABLE"
                ) from None
            if not _valid_trusted_context(context):
                raise WorkflowCompositionError("WORKFLOW_CONTEXT_UNAVAILABLE")
            if cancellation.is_set():
                raise WorkflowCompositionError("WORKFLOW_REQUEST_CANCELLED")
            try:
                run = self.engine.start_workflow(
                    definition,
                    payload["input"],
                    context,
                )
            except Exception as error:
                code = getattr(error, "code", None)
                if isinstance(code, str) and code.startswith("WORKFLOW_"):
                    raise WorkflowCompositionError(code) from None
                raise WorkflowCompositionError("WORKFLOW_EXECUTION_FAILED") from None
            return {
                "run_id": run.run_id,
                "status": run.status.value,
                "current_step_id": run.current_step_id,
                "revision": run.revision,
                "organization_id": run.organization_id,
                "trace_id": run.trace_id,
            }

        return {"runtime.workflow.start": start_workflow}


def _valid_trusted_context(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    organization_id = value.get("organization_id")
    trace_id = value.get("trace_id")
    actor = value.get("actor")
    return (
        _non_empty(organization_id)
        and _non_empty(trace_id)
        and isinstance(actor, Mapping)
        and _non_empty(actor.get("subject_id"))
        and actor.get("organization_id") == organization_id
    )


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
