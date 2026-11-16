from __future__ import annotations

from threading import Event

from shuliu_runtime.ipc.bootstrap import bootstrap_runtime
from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.policy.rules import PolicyOutcome, PolicyRule
from shuliu_runtime.tools.broker import IdentityPrincipal
from shuliu_runtime.tools.registry import ToolDefinition, ToolRegistry
from shuliu_runtime.workflow.definition import WorkflowDefinition

try:
    from shuliu_runtime.workflow.composition import ProductionWorkflowDependencies
except ImportError:
    ProductionWorkflowDependencies = None


def test_bootstrap_composes_persistent_workflow_with_real_tool_broker_and_trusted_context(tmp_path):
    assert ProductionWorkflowDependencies is not None, (
        "production Workflow-to-ToolBroker composition is not implemented"
    )

    calls = []

    class Identity:
        def resolve(self, actor):
            if actor.get("subject_id") != "runtime-user":
                return None
            return IdentityPrincipal(
                "runtime-user",
                "org-runtime",
                ("workflow.test",),
            )

    class Executor:
        def execute(self, tool_id, args, context, idempotency_key):
            calls.append((tool_id, dict(args), idempotency_key))
            return {"ok": True}

        def verify(self, tool_id, args, output, context):
            return output.get("ok") is True

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="foundation.workflow_test",
            version="1.0.0",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            permissions=("workflow.test",),
            risk_level=RiskLevel.LOW,
            idempotency_required=False,
        ),
        Executor(),
    )
    policy = PolicyEngine(
        [
            PolicyRule(
                rule_id="workflow-test-allow",
                action="foundation.workflow_test",
                risk=RiskLevel.LOW,
                decision=PolicyOutcome.ALLOW,
                organization_id="org-runtime",
                permission="workflow.test",
                privacy="INTERNAL",
            )
        ],
        policy_version="workflow-policy-v1",
    )
    definition = WorkflowDefinition.from_dict(
        {
            "workflow_id": "runtime-composition",
            "workflow_version_id": "v1",
            "input_schema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            "steps": [
                {
                    "id": "invoke",
                    "type": "tool",
                    "tool_id": "foundation.workflow_test",
                    "arguments": {"value": {"$ref": "input.value"}},
                    "next": "END",
                }
            ],
        }
    )
    dependencies = ProductionWorkflowDependencies(
        registry=registry,
        identity=Identity(),
        policy=policy,
        approvals=None,
        definition_resolver=lambda workflow_id, version_id: (
            definition
            if (workflow_id, version_id)
            == (definition.workflow_id, definition.workflow_version_id)
            else None
        ),
        run_context_provider=lambda: {
            "organization_id": "org-runtime",
            "trace_id": "trace-runtime",
            "actor": {
                "subject_id": "runtime-user",
                "organization_id": "org-runtime",
            },
            "privacy": "INTERNAL",
            "resource": {
                "organization_id": "org-runtime",
                "resource_type": "workflow",
                "resource_id": "runtime-composition",
            },
        },
    )

    bootstrap = bootstrap_runtime(tmp_path, workflow_dependencies=dependencies)
    assert bootstrap.workflow_runtime is not None
    handler = bootstrap.workflow_runtime.create_ipc_handlers()[
        "runtime.workflow.start"
    ]
    result = handler(
        {
            "workflow_id": "runtime-composition",
            "workflow_version_id": "v1",
            "input": {"value": "safe"},
        },
        Event(),
    )

    assert result["status"] == "SUCCEEDED"
    assert result["organization_id"] == "org-runtime"
    assert len(calls) == 1
    assert calls[0][0] == "foundation.workflow_test"
    assert calls[0][1] == {"value": "safe"}
    with bootstrap.database.connect() as connection:
        run_count = connection.execute(
            "SELECT COUNT(*) FROM workflow_runs"
        ).fetchone()[0]
        event_count = connection.execute(
            "SELECT COUNT(*) FROM outbox"
        ).fetchone()[0]
    assert run_count == 1
    assert event_count >= 2


def test_workflow_ipc_rejects_renderer_supplied_identity_or_organization(tmp_path):
    assert ProductionWorkflowDependencies is not None, (
        "production Workflow-to-ToolBroker composition is not implemented"
    )
    from shuliu_runtime.workflow.composition import WorkflowCompositionError

    context_calls = []

    class Identity:
        def resolve(self, actor):
            return None

    definition = WorkflowDefinition.from_dict(
        {
            "workflow_id": "ipc-scope-check",
            "workflow_version_id": "v1",
            "input_schema": {"type": "object"},
            "steps": [
                {
                    "id": "invoke",
                    "type": "tool",
                    "tool_id": "unavailable",
                    "arguments": {},
                    "next": "END",
                }
            ],
        }
    )
    dependencies = ProductionWorkflowDependencies(
        registry=ToolRegistry(),
        identity=Identity(),
        policy=PolicyEngine([], policy_version="deny-all-v1"),
        approvals=None,
        definition_resolver=lambda _workflow_id, _version_id: definition,
        run_context_provider=lambda: context_calls.append(True) or {},
    )
    bootstrap = bootstrap_runtime(tmp_path, workflow_dependencies=dependencies)
    assert bootstrap.workflow_runtime is not None
    handler = bootstrap.workflow_runtime.create_ipc_handlers()[
        "runtime.workflow.start"
    ]

    try:
        handler(
            {
                "workflow_id": "ipc-scope-check",
                "workflow_version_id": "v1",
                "input": {},
                "organization_id": "attacker-org",
                "actor": {"subject_id": "attacker"},
            },
            Event(),
        )
    except WorkflowCompositionError as error:
        assert error.code == "WORKFLOW_REQUEST_INVALID"
    else:
        raise AssertionError("renderer supplied identity was accepted")
    assert context_calls == []
