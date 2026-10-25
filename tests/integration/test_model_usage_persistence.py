from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.persistence.postgres import create_postgres_engine
from shuliu_runtime.models.gateway import ModelCall
from shuliu_runtime.models.postgres_usage import PostgresModelBudget, PostgresModelCallSink


SERVICE_ROOT = Path(__file__).resolve().parents[2] / "services" / "cloud-api"


def _test_database_url() -> str:
    database_url = os.environ.get("SHULIU_DATABASE_URL")
    if not database_url:
        pytest.skip("SHULIU_DATABASE_URL is not configured")
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "postgresql" or not (parsed.database or "").endswith("_test"):
        pytest.skip("integration tests require a PostgreSQL database whose name ends in _test")
    return database_url


def _upgrade(engine) -> None:
    with engine.connect() as connection:
        config = Config(str(SERVICE_ROOT / "alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()


@pytest.mark.integration
def test_model_call_is_durable_across_engine_recreation_and_idempotent():
    database_url = _test_database_url()
    organization_id = f"task29-{uuid4().hex}"
    call_id = uuid4().hex
    engine = create_postgres_engine(database_url)
    try:
        _upgrade(engine)
        call = ModelCall(
            call_id=call_id,
            organization_id=organization_id,
            trace_id=f"trace-{call_id}",
            requested_capability="fast_chat",
            resolved_provider="provider-a",
            resolved_model="model-a",
            model_version="model-a-v1",
            latency_ms=12.345,
            usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            cost=0.00001234,
            error_code=None,
            status="SUCCEEDED",
        )
        sink = PostgresModelCallSink(engine)
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(sink.record, (call, call)))
    finally:
        engine.dispose()

    reopened = create_postgres_engine(database_url)
    try:
        restored = PostgresModelCallSink(reopened).get(organization_id, call_id)
        assert restored is not None
        assert restored.model == "model-a"
        assert restored.cost == Decimal("0.00001234")
        assert restored.usage["total_tokens"] == 3
    finally:
        reopened.dispose()


@pytest.mark.integration
def test_concurrent_postgres_reservations_cannot_exceed_account_limit():
    database_url = _test_database_url()
    organization_id = f"task29-{uuid4().hex}"
    budget_ref = f"budget://{organization_id}/concurrency"
    engine = create_postgres_engine(database_url)
    try:
        _upgrade(engine)
        budget = PostgresModelBudget(engine)
        budget.configure_account(
            organization_id,
            budget_ref,
            currency="USD",
            limit_amount=Decimal("0.03000000"),
            reservation_amount=Decimal("0.03000000"),
        )

        def reserve(call_id: str) -> bool:
            return budget.reserve(
                organization_id,
                budget_ref,
                "provider-a",
                "fast_chat",
                f"trace-{call_id}",
                call_id=call_id,
            )

        call_ids = (uuid4().hex, uuid4().hex)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reserve, call_ids))

        assert sum(results) == 1
        account = budget.get_account(organization_id, budget_ref)
        assert account is not None
        assert account.reserved_amount == Decimal("0.03000000")
        assert account.spent_amount == Decimal("0E-8")
    finally:
        engine.dispose()
