from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from shuliu_runtime.approval.service import (
    Approval,
    ApprovalAuditEvent,
    ApprovalService,
    ApprovalStatus,
)
from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.risk import RiskLevel
from shuliu_runtime.policy.rules import (
    PolicyDecision,
    PolicyOutcome,
    PolicyRule,
)
from shuliu_runtime.tools.broker import (
    IdentityPrincipal,
    ToolBroker,
    ToolExecutionError,
    ToolExecutionStatus,
    VerificationStatus,
)
from shuliu_runtime.tools.idempotency import InMemoryIdempotencyStore
from shuliu_runtime.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolRegistryError,
)

from tests.fakes.fake_executor import FakeExecutor


def _definition(
    *, risk: RiskLevel = RiskLevel.MEDIUM, idempotency_required: bool = True
) -> ToolDefinition:
    return ToolDefinition(
        name="foundation.document.write",
        version="1.0.0",
        input_schema={
            "type": "object",
            "properties": {"document_id": {"type": "string"}},
            "required": ["document_id"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "evidence_ref": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                    "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}(?![\s\S])",
                },
            },
            "required": ["ok", "evidence_ref"],
            "additionalProperties": False,
        },
        permissions=("foundation.document.write",),
        risk_level=risk,
        idempotency_required=idempotency_required,
    )


def _actor() -> dict[str, str]:
    return {"subject_id": "agent-1", "organization_id": "org-1"}


def _context(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "organization_id": "org-1",
        "trace_id": "trace-1",
        "privacy": "INTERNAL",
        "resource": {
            "organization_id": "org-1",
            "resource_type": "document",
            "resource_id": "doc-1",
        },
    }
    result.update(overrides)
    return result


class RecordingIdentity:
    def __init__(
        self,
        order: list[str] | None = None,
        permissions: tuple[str, ...] = ("foundation.document.write",),
    ) -> None:
        self.order = order if order is not None else []
        self.permissions = permissions

    def resolve(self, actor: Mapping[str, object]) -> IdentityPrincipal | None:
        self.order.append("identity")
        if actor.get("subject_id") != "agent-1":
            return None
        return IdentityPrincipal(
            subject_id="agent-1",
            organization_id="org-1",
            permissions=self.permissions,
        )


class RecordingPolicy:
    def __init__(
        self,
        decision: PolicyOutcome,
        order: list[str] | None = None,
    ) -> None:
        self.decision = decision
        self.order = order if order is not None else []
        self.requests: list[dict[str, object]] = []

    def evaluate(self, **request: object) -> PolicyDecision:
        self.order.append("policy")
        self.requests.append(request)
        action = request["action"]
        context = request["context"]
        assert isinstance(action, Mapping)
        assert isinstance(context, Mapping)
        return PolicyDecision(
            decision=self.decision,
            risk=RiskLevel(str(action["risk"])),
            rule_id="test-rule",
            policy_version="policy-v1",
            reason="test decision",
            trace_id=str(context["trace_id"]),
        )


class RecordingApproval:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.consumed: list[tuple[str, dict[str, object]]] = []

    def consume(self, approval_id: str, action_context: Mapping[str, object]) -> object:
        self.order.append("approval")
        self.consumed.append((approval_id, dict(action_context)))
        return object()


def _broker(
    *,
    decision: PolicyOutcome = PolicyOutcome.ALLOW,
    risk: RiskLevel = RiskLevel.MEDIUM,
    executor: FakeExecutor | None = None,
    approvals: RecordingApproval | None = None,
    order: list[str] | None = None,
    idempotency_store: object | None = None,
    policy: object | None = None,
    identity: object | None = None,
    definition: ToolDefinition | None = None,
):
    order = order if order is not None else []
    registry = ToolRegistry()
    executor = executor or FakeExecutor(order=order)
    registry.register(definition or _definition(risk=risk), executor)
    policy = policy or RecordingPolicy(decision, order)
    events = []
    broker = ToolBroker(
        registry=registry,
        identity=identity or RecordingIdentity(order),
        policy=policy,
        approvals=approvals,
        idempotency_store=idempotency_store or InMemoryIdempotencyStore(),
        event_sink=lambda event: (order.append("event"), events.append(event)),
        clock=lambda: datetime(2026, 10, 17, tzinfo=UTC),
    )
    return broker, executor, policy, events, order


def test_broker_runs_identity_policy_executor_verification_then_event():
    order: list[str] = []
    broker, executor, _, events, _ = _broker(order=order)

    result = broker.execute(
        "foundation.document.write",
        {"document_id": "doc-1"},
        _actor(),
        _context(),
        idempotency_key="idem-1",
    )

    assert order == ["identity", "policy", "executor", "verify", "event"]
    assert result.execution_status is ToolExecutionStatus.SUCCEEDED
    assert result.verification_status is VerificationStatus.VERIFIED
    assert len(events) == 1
    assert events[0].event_type == "TOOL_EXECUTION_COMPLETED"
    assert executor.calls[0][1] == {"document_id": "doc-1"}


def test_executor_identity_is_resolved_by_broker_not_supplied_by_caller():
    broker, executor, _, _, _ = _broker()
    broker.execute(
        "foundation.document.write", {"document_id": "doc-1"}, _actor(),
        _context(_principal=IdentityPrincipal("forged", "org-1", ())),
        idempotency_key="identity-binding",
    )
    assert executor.calls[0][2]["_principal"] == IdentityPrincipal(
        "agent-1", "org-1", ("foundation.document.write",)
    )


def test_invalid_arguments_are_rejected_before_identity_policy_or_executor():
    broker, executor, policy, _, order = _broker()

    with pytest.raises(ToolExecutionError) as rejected:
        broker.execute(
            "foundation.document.write",
            {"unexpected": "value"},
            _actor(),
            _context(),
            idempotency_key="idem-1",
        )

    assert rejected.value.code == "TOOL_INPUT_INVALID"
    assert order == []
    assert not policy.requests
    assert not executor.calls


def test_policy_deny_never_reaches_approval_or_executor():
    broker, executor, _, events, order = _broker(decision=PolicyOutcome.DENY)

    with pytest.raises(ToolExecutionError) as rejected:
        broker.execute(
            "foundation.document.write",
            {"document_id": "doc-1"},
            _actor(),
            _context(),
            idempotency_key="idem-1",
        )

    assert rejected.value.code == "POLICY_DENIED"
    assert order == ["identity", "policy", "event"]
    assert events[0].event_type == "TOOL_EXECUTION_DENIED"
    assert not executor.calls


def test_missing_approval_never_reaches_executor():
    broker, executor, _, events, order = _broker(
        decision=PolicyOutcome.REQUIRE_APPROVAL,
        risk=RiskLevel.HIGH,
    )

    with pytest.raises(ToolExecutionError) as rejected:
        broker.execute(
            "foundation.document.write",
            {"document_id": "doc-1"},
            _actor(),
            _context(),
            idempotency_key="idem-1",
        )

    assert rejected.value.code == "APPROVAL_REQUIRED"
    assert order == ["identity", "policy", "event"]
    assert events[0].event_type == "TOOL_APPROVAL_REQUIRED"
    assert not executor.calls


def test_matching_approval_is_consumed_before_executor():
    order: list[str] = []
    approvals = RecordingApproval(order)
    broker, _, _, _, _ = _broker(
        decision=PolicyOutcome.REQUIRE_APPROVAL,
        risk=RiskLevel.HIGH,
        approvals=approvals,
        order=order,
    )

    broker.execute(
        "foundation.document.write",
        {"document_id": "doc-1"},
        _actor(),
        _context(
            approval_id="approval-1",
            target_approver_id="user-approver",
            action_summary="Write a document",
        ),
        idempotency_key="idem-1",
    )

    assert order == ["identity", "policy", "approval", "executor", "verify", "event"]
    assert approvals.consumed[0][0] == "approval-1"
    context = approvals.consumed[0][1]
    assert context["action_params"] == {"document_id": "doc-1"}
    assert context["risk"] == "HIGH"


def test_missing_idempotency_key_blocks_non_read_tool_before_executor():
    broker, executor, _, _, order = _broker(risk=RiskLevel.MEDIUM)

    with pytest.raises(ToolExecutionError) as rejected:
        broker.execute(
            "foundation.document.write",
            {"document_id": "doc-1"},
            _actor(),
            _context(),
            idempotency_key=None,
        )

    assert rejected.value.code == "IDEMPOTENCY_KEY_REQUIRED"
    assert order == ["identity", "policy"]
    assert not executor.calls


def test_required_idempotency_store_is_fail_closed_when_unavailable():
    order: list[str] = []
    registry = ToolRegistry()
    executor = FakeExecutor(order=order)
    registry.register(_definition(), executor)
    broker = ToolBroker(
        registry=registry,
        identity=RecordingIdentity(order),
        policy=RecordingPolicy(PolicyOutcome.ALLOW, order),
        approvals=None,
        event_sink=lambda event: order.append("event"),
        idempotency_store=None,
    )

    with pytest.raises(ToolExecutionError) as rejected:
        broker.execute(
            "foundation.document.write",
            {"document_id": "doc-1"},
            _actor(),
            _context(),
            idempotency_key="idem-1",
        )

    assert rejected.value.code == "IDEMPOTENCY_UNAVAILABLE"
    assert order == ["identity", "policy"]
    assert not executor.calls


def test_identity_scope_mismatch_fails_closed_before_policy():
    broker, executor, policy, _, order = _broker()
    context = _context(organization_id="org-other")

    with pytest.raises(ToolExecutionError) as rejected:
        broker.execute(
            "foundation.document.write",
            {"document_id": "doc-1"},
            _actor(),
            context,
            idempotency_key="idem-1",
        )

    assert rejected.value.code == "IDENTITY_SCOPE_MISMATCH"
    assert order == ["identity"]
    assert not policy.requests
    assert not executor.calls


def test_identity_without_declared_permission_fails_before_policy():
    order: list[str] = []
    broker, executor, policy, _, _ = _broker(
        order=order,
        identity=RecordingIdentity(order, permissions=()),
    )

    with pytest.raises(ToolExecutionError) as rejected:
        broker.execute(
            "foundation.document.write",
            {"document_id": "doc-1"},
            _actor(),
            _context(),
            idempotency_key="idem-1",
        )

    assert rejected.value.code == "IDENTITY_PERMISSION_DENIED"
    assert order == ["identity"]
    assert not policy.requests
    assert not executor.calls


def test_critical_tool_cannot_run_when_policy_returns_allow():
    broker, executor, _, events, order = _broker(
        decision=PolicyOutcome.ALLOW,
        risk=RiskLevel.CRITICAL,
    )

    with pytest.raises(ToolExecutionError) as rejected:
        broker.execute(
            "foundation.document.write",
            {"document_id": "doc-1"},
            _actor(),
            _context(),
            idempotency_key="idem-1",
        )

    assert rejected.value.code == "POLICY_APPROVAL_REQUIRED"
    assert order == ["identity", "policy", "event"]
    assert events[0].event_type == "TOOL_EXECUTION_DENIED"
    assert not executor.calls


class MemoryApprovalRepository:
    def __init__(self) -> None:
        self.records: dict[str, Approval] = {}
        self.events: list[ApprovalAuditEvent] = []

    def create(self, approval: Approval, event: ApprovalAuditEvent) -> None:
        self.records[approval.approval_id] = approval
        self.events.append(event)

    def get(self, approval_id: str, organization_id: str | None = None):
        record = self.records.get(approval_id)
        if record is not None and organization_id not in (None, record.organization_id):
            return None
        return record

    def transition(self, approval, expected_status, event):
        current = self.records.get(approval.approval_id)
        if current is None or current.status is not expected_status:
            return False
        self.records[approval.approval_id] = approval
        self.events.append(event)
        return True


def _approved_high_risk_call():
    now = datetime(2026, 10, 17, tzinfo=UTC)
    definition = _definition(risk=RiskLevel.HIGH)
    rule = PolicyRule(
        rule_id="approval-rule",
        action=definition.name,
        risk=RiskLevel.HIGH,
        decision=PolicyOutcome.REQUIRE_APPROVAL,
        organization_id="org-1",
        permission="foundation.document.write",
        resource_type="document",
        privacy="INTERNAL",
    )
    policy = PolicyEngine([rule], policy_version="policy-v1")
    actor = _actor()
    context = _context(
        target_approver_id="user-approver",
        action_summary="Write a document",
        run_id="run-1",
    )
    args = {"document_id": "doc-1"}
    subject = {
        "subject_id": "agent-1",
        "organization_id": "org-1",
        "permissions": ["foundation.document.write"],
    }
    action = {"action_id": definition.name, "risk": RiskLevel.HIGH.value}
    decision = policy.evaluate(
        subject=subject,
        action=action,
        resource=context["resource"],
        context={
            "organization_id": "org-1",
            "trace_id": "trace-1",
            "privacy": "INTERNAL",
        },
    )
    repository = MemoryApprovalRepository()
    approvals = ApprovalService(
        repository=repository,
        authorize_approver=lambda **values: values["approver_id"] == "user-approver",
        approval_ttl=timedelta(minutes=10),
        clock=lambda: now,
    )
    approval_context = {
        "organization_id": "org-1",
        "requester_id": "agent-1",
        "target_approver_id": "user-approver",
        "action_id": definition.name,
        "action_summary": "Write a document",
        "resource": context["resource"],
        "risk": "HIGH",
        "action_params": args,
        "run_id": "run-1",
        "trace_id": "trace-1",
    }
    approval = approvals.request_approval(approval_context, decision)
    approvals.decide(
        approval.approval_id,
        approver_id="user-approver",
        decision="APPROVE",
    )
    executor = FakeExecutor()
    registry = ToolRegistry()
    registry.register(definition, executor)
    broker = ToolBroker(
        registry=registry,
        identity=RecordingIdentity(),
        policy=policy,
        approvals=approvals,
        event_sink=lambda _: None,
        idempotency_store=InMemoryIdempotencyStore(),
        clock=lambda: now,
    )
    return approvals, approval, broker, executor, args, actor, context


def test_tool_broker_consumes_real_context_bound_approval_before_executor():
    approvals, approval, broker, executor, args, actor, context = (
        _approved_high_risk_call()
    )

    result = broker.execute(
        "foundation.document.write",
        args,
        actor,
        {**context, "approval_id": approval.approval_id},
        idempotency_key="idem-approved-1",
    )

    assert result.verification_status is VerificationStatus.VERIFIED
    assert approvals.get(approval.approval_id).status is ApprovalStatus.CONSUMED
    assert len(executor.calls) == 1


def test_changed_action_cannot_reuse_real_approval():
    approvals, approval, broker, executor, _, actor, context = (
        _approved_high_risk_call()
    )

    with pytest.raises(ToolExecutionError) as changed:
        broker.execute(
            "foundation.document.write",
            {"document_id": "doc-changed"},
            actor,
            {**context, "approval_id": approval.approval_id},
            idempotency_key="idem-approved-changed",
        )

    assert changed.value.code == "APPROVAL_CONTEXT_CHANGED"
    assert approvals.get(approval.approval_id).status is ApprovalStatus.INVALIDATED
    assert not executor.calls


def test_registry_rejects_external_json_schema_references():
    definition = _definition()
    unsafe = ToolDefinition(
        name="foundation.document.write.remote-schema",
        version=definition.version,
        input_schema={"$ref": "https://example.invalid/schema.json"},
        output_schema=definition.output_schema,
        permissions=definition.permissions,
        risk_level=definition.risk_level,
        idempotency_required=definition.idempotency_required,
    )

    with pytest.raises(ToolRegistryError) as rejected:
        ToolRegistry().register(unsafe, FakeExecutor())

    assert rejected.value.code == "TOOL_SCHEMA_INVALID"


def test_registry_rejects_non_idempotent_write_definition():
    definition = _definition(idempotency_required=False)

    with pytest.raises(ToolRegistryError) as rejected:
        ToolRegistry().register(definition, FakeExecutor())

    assert rejected.value.code == "TOOL_DEFINITION_INVALID"


@pytest.mark.parametrize(
    "output_schema",
    [
        {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "evidence_ref": {"type": "string", "minLength": 1},
            },
            "required": ["ok"],
            "additionalProperties": False,
        },
    ],
)
def test_registry_rejects_idempotent_output_without_required_safe_result_reference(
    output_schema,
):
    definition = replace(
        _definition(),
        output_schema=output_schema,
    )

    with pytest.raises(ToolRegistryError) as rejected:
        ToolRegistry().register(definition, FakeExecutor())

    assert rejected.value.code == "TOOL_DEFINITION_INVALID"


def test_registry_rejects_nested_requirement_outside_safe_replay_projection():
    base = _definition()
    unsafe_schema = {
        **base.output_schema,
        "allOf": [{"required": ["private_body"]}],
    }
    definition = replace(base, output_schema=unsafe_schema)

    with pytest.raises(ToolRegistryError) as rejected:
        ToolRegistry().register(definition, FakeExecutor())

    assert rejected.value.code == "TOOL_DEFINITION_INVALID"


def test_registry_rejects_min_properties_only_satisfied_by_dropped_output():
    base = _definition()
    unsafe_schema = {
        **base.output_schema,
        "properties": {
            **base.output_schema["properties"],
            "observation": {"type": "string"},
        },
        "minProperties": 3,
    }
    definition = replace(base, output_schema=unsafe_schema)

    with pytest.raises(ToolRegistryError) as rejected:
        ToolRegistry().register(definition, FakeExecutor())

    assert rejected.value.code == "TOOL_DEFINITION_INVALID"


def test_registry_rejects_oneof_that_depends_on_dropped_output_properties():
    base = _definition()
    unsafe_schema = {
        **base.output_schema,
        "properties": {
            **base.output_schema["properties"],
            "observation": {"type": "string"},
        },
        "oneOf": [
            {"properties": {"observation": {"const": "first"}}},
            {"properties": {"observation": {"const": "second"}}},
        ],
    }
    definition = replace(base, output_schema=unsafe_schema)

    with pytest.raises(ToolRegistryError) as rejected:
        ToolRegistry().register(definition, FakeExecutor())

    assert rejected.value.code == "TOOL_DEFINITION_INVALID"


def test_registry_rejects_result_reference_schema_broader_than_safe_projection():
    base = _definition()
    unsafe_schema = {
        **base.output_schema,
        "properties": {
            **base.output_schema["properties"],
            "evidence_ref": {"type": "string", "minLength": 1},
        },
    }
    definition = replace(base, output_schema=unsafe_schema)

    with pytest.raises(ToolRegistryError) as rejected:
        ToolRegistry().register(definition, FakeExecutor())

    assert rejected.value.code == "TOOL_DEFINITION_INVALID"


def test_idempotent_output_schema_accepts_only_persistable_reference_values():
    registry = ToolRegistry()
    registry.register(_definition(), FakeExecutor())

    assert registry.validate_output(
        "foundation.document.write",
        {"ok": True, "evidence_ref": "evidence-1:result"},
    )
    assert not registry.validate_output(
        "foundation.document.write",
        {"ok": True, "evidence_ref": "https://host.example/evidence"},
    )
    assert not registry.validate_output(
        "foundation.document.write",
        {"ok": True, "evidence_ref": "evidence-1\n"},
    )


def test_registry_rejects_applicator_that_makes_required_reference_impossible():
    base = _definition()
    unsafe_schema = {
        **base.output_schema,
        "allOf": [
            {"properties": {"evidence_ref": {"maxLength": 0}}},
        ],
    }
    definition = replace(base, output_schema=unsafe_schema)

    with pytest.raises(ToolRegistryError) as rejected:
        ToolRegistry().register(definition, FakeExecutor())

    assert rejected.value.code == "TOOL_DEFINITION_INVALID"


def test_registry_rejects_nested_applicator_inside_required_reference_schema():
    base = _definition()
    unsafe_schema = {
        **base.output_schema,
        "properties": {
            **base.output_schema["properties"],
            "evidence_ref": {
                **base.output_schema["properties"]["evidence_ref"],
                "allOf": [{"maxLength": 0}],
            },
        },
    }
    definition = replace(base, output_schema=unsafe_schema)

    with pytest.raises(ToolRegistryError) as rejected:
        ToolRegistry().register(definition, FakeExecutor())

    assert rejected.value.code == "TOOL_DEFINITION_INVALID"


@pytest.mark.parametrize(
    "keyword,constraint",
    [
        ("patternProperties", {"^evidence_ref$": {"maxLength": 0}}),
        ("propertyNames", {"pattern": "^x$"}),
    ],
)
def test_registry_rejects_root_schema_that_invalidates_safe_replay_fields(
    keyword, constraint
):
    base = _definition()
    unsafe_schema = {**base.output_schema, keyword: constraint}
    definition = replace(base, output_schema=unsafe_schema)

    with pytest.raises(ToolRegistryError) as rejected:
        ToolRegistry().register(definition, FakeExecutor())

    assert rejected.value.code == "TOOL_DEFINITION_INVALID"


@pytest.mark.parametrize(
    "placement",
    ["root", "all_of"],
)
def test_registry_rejects_max_properties_below_required_replay_fields(placement):
    base = _definition()
    unsafe_schema = dict(base.output_schema)
    if placement == "root":
        unsafe_schema["maxProperties"] = 0
    else:
        unsafe_schema["allOf"] = [{"maxProperties": 0}]
    definition = replace(base, output_schema=unsafe_schema)

    with pytest.raises(ToolRegistryError) as rejected:
        ToolRegistry().register(definition, FakeExecutor())

    assert rejected.value.code == "TOOL_DEFINITION_INVALID"


def test_registry_rejects_required_status_value_dropped_by_replay_projection():
    base = _definition()
    unsafe_schema = {
        **base.output_schema,
        "properties": {
            **base.output_schema["properties"],
            "status": {"const": "CUSTOM"},
        },
        "required": [*base.output_schema["required"], "status"],
    }
    definition = replace(base, output_schema=unsafe_schema)

    with pytest.raises(ToolRegistryError) as rejected:
        ToolRegistry().register(definition, FakeExecutor())

    assert rejected.value.code == "TOOL_DEFINITION_INVALID"


def test_registry_rejects_optional_projected_status_outside_replay_domain():
    base = _definition()
    unsafe_schema = {
        **base.output_schema,
        "properties": {
            **base.output_schema["properties"],
            "status": {"enum": ["VERIFIED", "CUSTOM"]},
        },
    }
    definition = replace(base, output_schema=unsafe_schema)

    with pytest.raises(ToolRegistryError) as rejected:
        ToolRegistry().register(definition, FakeExecutor())

    assert rejected.value.code == "TOOL_DEFINITION_INVALID"


@pytest.mark.parametrize("placement", ["root", "all_of"])
def test_registry_rejects_empty_safe_error_code_length_range(placement):
    base = _definition()
    error_code = {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
        "pattern": r"^[A-Z][A-Z0-9_]{0,127}(?![\s\S])",
    }
    if placement == "root":
        error_code.update(minLength=129, maxLength=128)
    else:
        error_code["allOf"] = [{"minLength": 129}, {"maxLength": 128}]
    unsafe_schema = {
        **base.output_schema,
        "properties": {**base.output_schema["properties"], "error_code": error_code},
    }
    definition = replace(base, output_schema=unsafe_schema)

    with pytest.raises(ToolRegistryError) as rejected:
        ToolRegistry().register(definition, FakeExecutor())

    assert rejected.value.code == "TOOL_DEFINITION_INVALID"


@pytest.mark.parametrize("placement", ["root_all_of", "nested_all_of"])
def test_registry_rejects_root_applicator_that_invalidates_optional_error_code(
    placement,
):
    base = _definition()
    error_code = {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
        "pattern": r"^[A-Z][A-Z0-9_]{0,127}(?![\s\S])",
    }
    unsafe_branch = {"properties": {"error_code": {"minLength": 129}}}
    unsafe_schema = {
        **base.output_schema,
        "properties": {**base.output_schema["properties"], "error_code": error_code},
        "allOf": [
            unsafe_branch
            if placement == "root_all_of"
            else {"allOf": [unsafe_branch]}
        ],
    }
    definition = replace(base, output_schema=unsafe_schema)

    with pytest.raises(ToolRegistryError) as rejected:
        ToolRegistry().register(definition, FakeExecutor())

    assert rejected.value.code == "TOOL_DEFINITION_INVALID"


def test_registry_rejects_condition_whose_property_count_changes_after_projection():
    base = _definition()
    safe_properties = base.output_schema["properties"]
    unsafe_schema = {
        **base.output_schema,
        "properties": {
            **safe_properties,
            "status": {
                "enum": ["VERIFIED", "UNVERIFIED", "FAILED", "OBSERVED", "UNAVAILABLE", "READY", "SUCCEEDED"]
            },
            "private_body": {"type": "string"},
        },
        "required": ["evidence_ref"],
        "if": {"maxProperties": 2},
        "then": {"properties": {"status": {"const": "FAILED"}}},
        "else": {"properties": {"status": {"const": "VERIFIED"}}},
    }
    definition = replace(base, output_schema=unsafe_schema)

    with pytest.raises(ToolRegistryError) as rejected:
        ToolRegistry().register(definition, FakeExecutor())

    assert rejected.value.code == "TOOL_DEFINITION_INVALID"


def test_registry_rejects_condition_dependent_schema_on_projected_away_property():
    base = _definition()
    unsafe_schema = {
        **base.output_schema,
        "properties": {
            **base.output_schema["properties"],
            "status": {
                "enum": ["VERIFIED", "UNVERIFIED", "FAILED", "OBSERVED", "UNAVAILABLE", "READY", "SUCCEEDED"]
            },
            "private_body": {"type": "string"},
        },
        "required": ["evidence_ref"],
        "if": {"dependentSchemas": {"private_body": False}},
        "then": {"properties": {"status": {"const": "FAILED"}}},
        "else": {"properties": {"status": {"const": "VERIFIED"}}},
    }
    definition = replace(base, output_schema=unsafe_schema)

    with pytest.raises(ToolRegistryError) as rejected:
        ToolRegistry().register(definition, FakeExecutor())

    assert rejected.value.code == "TOOL_DEFINITION_INVALID"


def test_registry_rejects_nested_if_then_condition_that_changes_after_projection():
    base = _definition()
    unsafe_schema = {
        **base.output_schema,
        "properties": {
            **base.output_schema["properties"],
            "status": {
                "enum": ["VERIFIED", "UNVERIFIED", "FAILED", "OBSERVED", "UNAVAILABLE", "READY", "SUCCEEDED"]
            },
            "private_body": {"type": "string"},
        },
        "if": {
            "if": {},
            "then": {"maxProperties": 3},
        },
        "then": {"properties": {"status": {"const": "FAILED"}}},
        "else": {"properties": {"status": {"const": "VERIFIED"}}},
    }
    definition = replace(base, output_schema=unsafe_schema)

    with pytest.raises(ToolRegistryError) as rejected:
        ToolRegistry().register(definition, FakeExecutor())

    assert rejected.value.code == "TOOL_DEFINITION_INVALID"


@pytest.mark.parametrize(
    "keyword,constraint,accepted",
    [
        ("const", "session-1", True),
        ("enum", ["session-1"], True),
        ("const", "session/1", False),
        ("enum", ["session-1", "session/1"], False),
    ],
)
def test_registry_validates_session_id_const_and_enum_safely(
    keyword, constraint, accepted
):
    base = _definition()
    session_id = {
        "type": ["string", "null"],
        "minLength": 1,
        "maxLength": 128,
        "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}(?![\s\S])",
        "allOf": [{keyword: constraint}],
    }
    output_schema = {
        **base.output_schema,
        "properties": {**base.output_schema["properties"], "session_id": session_id},
    }
    definition = replace(base, output_schema=output_schema)

    if accepted:
        ToolRegistry().register(definition, FakeExecutor())
    else:
        with pytest.raises(ToolRegistryError) as rejected:
            ToolRegistry().register(definition, FakeExecutor())
        assert rejected.value.code == "TOOL_DEFINITION_INVALID"


def test_unverified_execution_is_not_reported_as_verified_success():
    broker, _, _, events, _ = _broker(executor=FakeExecutor(verified=False))

    result = broker.execute(
        "foundation.document.write",
        {"document_id": "doc-1"},
        _actor(),
        _context(),
        idempotency_key="idem-1",
    )

    assert result.execution_status is ToolExecutionStatus.SUCCEEDED
    assert result.verification_status is VerificationStatus.FAILED
    assert events[0].event_type == "TOOL_EXECUTION_UNVERIFIED"


def test_output_schema_failure_is_not_reported_as_verified_success():
    broker, _, _, events, _ = _broker(
        executor=FakeExecutor(
            output={"ok": True, "unexpected": "not in contract", "evidence_ref": "evidence-invalid"}
        )
    )

    result = broker.execute(
        "foundation.document.write",
        {"document_id": "doc-1"},
        _actor(),
        _context(),
        idempotency_key="idem-1",
    )

    assert result.execution_status is ToolExecutionStatus.SUCCEEDED
    assert result.verification_status is VerificationStatus.FAILED
    assert result.error_code == "TOOL_OUTPUT_INVALID"
    assert events[0].event_type == "TOOL_EXECUTION_UNVERIFIED"


def test_unsafe_trace_id_is_rejected_before_idempotent_side_effect():
    base = _definition()
    output_schema = {
        **base.output_schema,
        "properties": {
            **base.output_schema["properties"],
            "trace_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}(?![\s\S])",
            },
        },
        "required": [*base.output_schema["required"], "trace_id"],
    }
    definition = replace(base, output_schema=output_schema)
    executor = FakeExecutor(
        output={"ok": True, "evidence_ref": "evidence-1", "trace_id": "trace/a"}
    )
    broker, executor, _, _, _ = _broker(executor=executor, definition=definition)

    with pytest.raises(ToolExecutionError) as rejected:
        broker.execute(
            "foundation.document.write",
            {"document_id": "doc-1"},
            _actor(),
            _context(trace_id="trace/a"),
            idempotency_key="unsafe-trace",
        )

    assert rejected.value.code == "TOOL_CONTEXT_INVALID"
    assert not executor.calls


def test_non_idempotent_read_tool_keeps_existing_nonempty_trace_id_contract():
    definition = _definition(
        risk=RiskLevel.LOW,
        idempotency_required=False,
    )
    broker, executor, _, _, _ = _broker(
        risk=RiskLevel.LOW,
        definition=definition,
    )

    result = broker.execute(
        "foundation.document.write",
        {"document_id": "doc-1"},
        _actor(),
        _context(trace_id="trace/a"),
        idempotency_key=None,
    )

    assert result.execution_status is ToolExecutionStatus.SUCCEEDED
    assert len(executor.calls) == 1


def test_policy_exception_fails_closed_before_executor():
    order: list[str] = []

    class BrokenPolicy:
        def evaluate(self, **request: object) -> PolicyDecision:
            order.append("policy")
            raise RuntimeError("policy store unavailable")

    broker, executor, _, _, _ = _broker(order=order, policy=BrokenPolicy())

    with pytest.raises(ToolExecutionError) as rejected:
        broker.execute(
            "foundation.document.write",
            {"document_id": "doc-1"},
            _actor(),
            _context(),
            idempotency_key="idem-1",
        )

    assert rejected.value.code == "POLICY_UNAVAILABLE"
    assert not executor.calls
