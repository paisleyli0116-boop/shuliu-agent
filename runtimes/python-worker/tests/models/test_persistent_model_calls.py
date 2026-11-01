from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
import pytest

from shuliu_runtime.models.capability import ModelProviderConfig
from shuliu_runtime.models.gateway import ModelCall, ModelGateway, ModelGatewayError, ModelProviderError, ModelRequest, ProviderResult
from shuliu_runtime.models.postgres_usage import ModelUsageConflictError, PostgresModelBudget, PostgresModelCallSink
from shuliu_runtime.policy.engine import PolicyEngine
from shuliu_runtime.policy.rules import PolicyRule


SERVICE_ROOT = Path(__file__).resolve().parents[4] / "services" / "cloud-api"


def _engine_with_schema():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as connection:
        config = Config(str(SERVICE_ROOT / "alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    return engine


def _call(*, organization_id: str = "org-a", cost: float = 0.12345678) -> ModelCall:
    return ModelCall(
        call_id="call-1",
        organization_id=organization_id,
        trace_id="trace-1",
        requested_capability="fast_chat",
        resolved_provider="provider-a",
        resolved_model="model-a",
        model_version="model-a-v2",
        latency_ms=12.345,
        usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        cost=cost,
        error_code=None,
        status="SUCCEEDED",
    )


def test_model_call_survives_sink_recreation_and_duplicate_record_is_idempotent():
    engine = _engine_with_schema()
    try:
        sink = PostgresModelCallSink(engine)
        call = _call()
        sink.record(call)
        sink.record(call)

        restored = PostgresModelCallSink(engine).get("org-a", "call-1")
        assert restored is not None
        assert restored.call_id == "call-1"
        assert restored.organization_id == "org-a"
        assert restored.usage == call.usage
        assert restored.cost == Decimal("0.12345678")
        assert restored.status == "SUCCEEDED"
        assert restored.created_at.tzinfo is not None
    finally:
        engine.dispose()


def test_model_call_identity_is_organization_scoped_and_conflicting_replay_is_rejected():
    engine = _engine_with_schema()
    try:
        sink = PostgresModelCallSink(engine)
        sink.record(_call(organization_id="org-a"))
        sink.record(_call(organization_id="org-b"))

        assert sink.get("org-a", "call-1").organization_id == "org-a"
        assert sink.get("org-b", "call-1").organization_id == "org-b"

        conflicting = _call(organization_id="org-a", cost=0.5)
        try:
            sink.record(conflicting)
        except ModelUsageConflictError:
            pass
        else:
            raise AssertionError("conflicting call_id replay must fail closed")
    finally:
        engine.dispose()


def test_model_call_rejects_unrecognized_usage_fields_before_persistence():
    from dataclasses import replace

    engine = _engine_with_schema()
    try:
        unsafe = replace(_call(), usage={"untrusted_payload_fragment": 1})
        with pytest.raises(ValueError, match="usage"):
            PostgresModelCallSink(engine).record(unsafe)
        assert PostgresModelCallSink(engine).get("org-a", "call-1") is None
    finally:
        engine.dispose()


def test_gateway_persists_actual_call_and_prevents_replaying_same_call_id():
    engine = _engine_with_schema()
    try:
        organization_id = "org-a"
        budget_ref = "budget://org-a/chat"
        budget = PostgresModelBudget(engine)
        budget.configure_account(
            organization_id,
            budget_ref,
            currency="USD",
            limit_amount=Decimal("1.00"),
            reservation_amount=Decimal("0.10"),
        )
        sink = PostgresModelCallSink(engine)

        class _Adapter:
            def __init__(self):
                self.calls = 0

            def invoke(self, provider, request, api_key=None):
                self.calls += 1
                return ProviderResult(
                    output="response", usage={"prompt_tokens": 2, "completion_tokens": 1},
                    cost=0.01234567, model_version="model-v3",
                )

        adapter = _Adapter()
        provider = ModelProviderConfig("provider-a", "model-a", frozenset({"fast_chat"}), 1, False)
        policy = PolicyEngine(
            [
                PolicyRule(
                    rule_id="allow-model", action="model.invoke.fast_chat.provider-a",
                    risk="LOW", decision="ALLOW", organization_id=organization_id,
                    permission="foundation.model.invoke", resource_type="model_provider",
                    classification="INTERNAL", privacy="INTERNAL",
                )
            ],
            policy_version="policy-v1",
        )

        class _Health:
            def is_healthy(self, provider_id):
                return True

        gateway = ModelGateway(
            providers=(provider,), adapter=adapter, policy=policy, budget=budget,
            health=_Health(), model_calls=sink,
        )
        request = ModelRequest(
            call_id="logical-call-1", capability="fast_chat", privacy="INTERNAL",
            budget_ref=budget_ref, input="hello", trace_id="trace-1",
            organization_id=organization_id, subject_id="user-a",
            permissions=("foundation.model.invoke",),
        )

        result = gateway.complete(request)
        call_id = uuid5(NAMESPACE_URL, "shuliu:model-call:logical-call-1:provider-a").hex
        record = sink.get(organization_id, call_id)
        assert result.output == "response"
        assert record is not None
        assert record.model_version == "model-v3"
        assert record.cost == Decimal("0.01234567")
        assert budget.get_reservation(organization_id, call_id).status == "SETTLED"

        with pytest.raises(ModelGatewayError):
            gateway.complete(request)
        assert adapter.calls == 1
        assert budget.get_account(organization_id, budget_ref).spent_amount == Decimal("0.01234567")
    finally:
        engine.dispose()


def test_gateway_records_actual_budget_overage_then_blocks_followup_before_provider_call():
    engine = _engine_with_schema()
    try:
        organization_id = "org-budget-overage"
        budget_ref = "budget://org-budget-overage/embedding"
        budget = PostgresModelBudget(engine)
        budget.configure_account(
            organization_id,
            budget_ref,
            currency="USD",
            limit_amount=Decimal("0.10"),
            reservation_amount=Decimal("0.06"),
        )
        sink = PostgresModelCallSink(engine)

        class _Adapter:
            calls = 0

            def invoke(self, provider, request, api_key=None):
                self.calls += 1
                return ProviderResult(
                    output=[[0.1, 0.2]],
                    usage={"prompt_tokens": 5, "total_tokens": 5},
                    cost=0.12,
                    model_version="embedding-v1",
                )

        class _Health:
            def is_healthy(self, provider_id):
                return True

        adapter = _Adapter()
        provider = ModelProviderConfig("provider-a", "embedding-v1", frozenset({"embedding"}), 1, False)
        policy = PolicyEngine(
            [
                PolicyRule(
                    rule_id="allow-embedding",
                    action="model.invoke.embedding.provider-a",
                    risk="LOW",
                    decision="ALLOW",
                    organization_id=organization_id,
                    permission="foundation.model.invoke",
                    resource_type="model_provider",
                    classification="INTERNAL",
                    privacy="INTERNAL",
                )
            ],
            policy_version="policy-v1",
        )
        gateway = ModelGateway(
            providers=(provider,),
            adapter=adapter,
            policy=policy,
            budget=budget,
            health=_Health(),
            model_calls=sink,
        )
        common_request = {
            "capability": "embedding",
            "privacy": "INTERNAL",
            "budget_ref": budget_ref,
            "input": ["public fixture"],
            "organization_id": organization_id,
            "subject_id": "user-a",
            "permissions": ("foundation.model.invoke",),
        }

        first = ModelRequest(
            **common_request,
            call_id="embedding-overage-1",
            trace_id="trace-overage-1",
        )
        result = gateway.complete(first)
        first_call_id = uuid5(
            NAMESPACE_URL, "shuliu:model-call:embedding-overage-1:provider-a"
        ).hex

        assert result.cost == 0.12
        assert budget.get_account(organization_id, budget_ref).spent_amount == Decimal("0.12000000")
        assert budget.get_reservation(organization_id, first_call_id).status == "SETTLED"
        assert sink.get(organization_id, first_call_id).cost == Decimal("0.12000000")

        second = ModelRequest(
            **common_request,
            call_id="embedding-overage-2",
            trace_id="trace-overage-2",
        )
        with pytest.raises(ModelGatewayError) as error:
            gateway.complete(second)

        assert error.value.code == "MODEL_BUDGET_EXHAUSTED"
        assert error.value.request_status == "not_sent"
        assert error.value.failure_stage == "budget"
        assert adapter.calls == 1
    finally:
        engine.dispose()


def test_gateway_rolls_back_budget_settlement_when_model_call_write_fails():
    engine = _engine_with_schema()
    try:
        organization_id = "org-atomic"
        budget_ref = "budget://org-atomic/chat"
        budget = PostgresModelBudget(engine)
        budget.configure_account(
            organization_id,
            budget_ref,
            currency="USD",
            limit_amount=Decimal("1.00"),
            reservation_amount=Decimal("0.10"),
        )
        sink = PostgresModelCallSink(engine)

        def fail_model_call_insert(connection, cursor, statement, parameters, context, executemany):
            if "INSERT INTO model_calls" in statement:
                raise RuntimeError("injected test failure")

        event.listen(engine, "before_cursor_execute", fail_model_call_insert)

        class _Adapter:
            def invoke(self, provider, request, api_key=None):
                return ProviderResult(
                    output="response",
                    usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                    cost=0.01234567,
                    model_version="model-v3",
                )

        class _Health:
            def is_healthy(self, provider_id):
                return True

        provider = ModelProviderConfig("provider-a", "model-a", frozenset({"fast_chat"}), 1, False)
        policy = PolicyEngine(
            [
                PolicyRule(
                    rule_id="allow-model",
                    action="model.invoke.fast_chat.provider-a",
                    risk="LOW",
                    decision="ALLOW",
                    organization_id=organization_id,
                    permission="foundation.model.invoke",
                    resource_type="model_provider",
                    classification="INTERNAL",
                    privacy="INTERNAL",
                )
            ],
            policy_version="policy-v1",
        )
        gateway = ModelGateway(
            providers=(provider,),
            adapter=_Adapter(),
            policy=policy,
            budget=budget,
            health=_Health(),
            model_calls=sink,
        )
        request = ModelRequest(
            call_id="atomic-call",
            capability="fast_chat",
            privacy="INTERNAL",
            budget_ref=budget_ref,
            input="hello",
            trace_id="trace-atomic",
            organization_id=organization_id,
            subject_id="user-a",
            permissions=("foundation.model.invoke",),
        )

        with pytest.raises(ModelGatewayError):
            gateway.complete(request)

        call_id = uuid5(NAMESPACE_URL, "shuliu:model-call:atomic-call:provider-a").hex
        account = budget.get_account(organization_id, budget_ref)
        reservation = budget.get_reservation(organization_id, call_id)
        assert account is not None
        assert account.spent_amount == Decimal("0E-8")
        assert account.reserved_amount == Decimal("0.10000000")
        assert reservation is not None
        assert reservation.status == "RECONCILIATION_REQUIRED"
        assert sink.get(organization_id, call_id) is None
    finally:
        engine.dispose()


def test_gateway_retains_unknown_reservation_for_reconciliation_if_atomic_call_write_fails():
    engine = _engine_with_schema()
    try:
        organization_id = "org-unknown-atomic"
        budget_ref = "budget://org-unknown-atomic/chat"
        budget = PostgresModelBudget(engine)
        budget.configure_account(
            organization_id,
            budget_ref,
            currency="USD",
            limit_amount=Decimal("1.00"),
            reservation_amount=Decimal("0.10"),
        )
        sink = PostgresModelCallSink(engine)

        def fail_model_call_insert(connection, cursor, statement, parameters, context, executemany):
            if "INSERT INTO model_calls" in statement:
                raise RuntimeError("injected test failure")

        event.listen(engine, "before_cursor_execute", fail_model_call_insert)

        class _Adapter:
            def invoke(self, provider, request, api_key=None):
                raise ModelProviderError(
                    "MODEL_TIMEOUT", request_status="unknown", failure_stage="provider_transport"
                )

        class _Health:
            def is_healthy(self, provider_id):
                return True

        provider = ModelProviderConfig("provider-a", "model-a", frozenset({"fast_chat"}), 1, False)
        policy = PolicyEngine(
            [
                PolicyRule(
                    rule_id="allow-model",
                    action="model.invoke.fast_chat.provider-a",
                    risk="LOW",
                    decision="ALLOW",
                    organization_id=organization_id,
                    permission="foundation.model.invoke",
                    resource_type="model_provider",
                    classification="INTERNAL",
                    privacy="INTERNAL",
                )
            ],
            policy_version="policy-v1",
        )
        gateway = ModelGateway(
            providers=(provider,),
            adapter=_Adapter(),
            policy=policy,
            budget=budget,
            health=_Health(),
            model_calls=sink,
        )
        request = ModelRequest(
            call_id="unknown-atomic-call",
            capability="fast_chat",
            privacy="INTERNAL",
            budget_ref=budget_ref,
            input="hello",
            trace_id="trace-unknown-atomic",
            organization_id=organization_id,
            subject_id="user-a",
            permissions=("foundation.model.invoke",),
        )

        with pytest.raises(ModelGatewayError) as error:
            gateway.complete(request)

        call_id = uuid5(NAMESPACE_URL, "shuliu:model-call:unknown-atomic-call:provider-a").hex
        account = budget.get_account(organization_id, budget_ref)
        reservation = budget.get_reservation(organization_id, call_id)
        assert error.value.request_status == "unknown"
        assert account is not None
        assert account.spent_amount == Decimal("0E-8")
        assert account.reserved_amount == Decimal("0.10000000")
        assert reservation is not None
        assert reservation.status == "RECONCILIATION_REQUIRED"
        assert sink.get(organization_id, call_id) is None
    finally:
        engine.dispose()
