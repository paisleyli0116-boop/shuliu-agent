from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from shuliu_runtime.models.postgres_usage import PostgresModelBudget


SERVICE_ROOT = Path(__file__).resolve().parents[4] / "services" / "cloud-api"


def _budget_with_schema():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as connection:
        config = Config(str(SERVICE_ROOT / "alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    return engine, PostgresModelBudget(engine)


def test_reservations_are_atomic_scoped_and_settlement_is_idempotent():
    engine, budget = _budget_with_schema()
    try:
        budget.configure_account(
            "org-a", "budget://org-a/chat", currency="USD",
            limit_amount=Decimal("0.10000000"),
            reservation_amount=Decimal("0.06000000"),
        )
        budget.configure_account(
            "org-b", "budget://org-a/chat", currency="USD",
            limit_amount=Decimal("0.10000000"),
            reservation_amount=Decimal("0.06000000"),
        )

        assert budget.reserve(
            "org-a", "budget://org-a/chat", "provider-a", "fast_chat", "trace-1", call_id="call-a"
        ) is True
        assert budget.reserve(
            "org-a", "budget://org-a/chat", "provider-a", "fast_chat", "trace-2", call_id="call-b"
        ) is False
        assert budget.reserve(
            "org-b", "budget://org-a/chat", "provider-a", "fast_chat", "trace-3", call_id="call-a"
        ) is True

        actual = Decimal("0.02345678")
        budget.settle("org-a", "budget://org-a/chat", "call-a", {"total_tokens": 5}, actual)
        budget.settle("org-a", "budget://org-a/chat", "call-a", {"total_tokens": 5}, actual)
        account = budget.get_account("org-a", "budget://org-a/chat")
        assert account.spent_amount == actual
        assert account.reserved_amount == Decimal("0E-8")
        assert budget.get_reservation("org-a", "call-a").status == "SETTLED"
    finally:
        engine.dispose()


def test_unknown_provider_outcome_keeps_reservation_for_reconciliation():
    engine, budget = _budget_with_schema()
    try:
        budget.configure_account(
            "org-a", "budget://org-a/chat", currency="USD",
            limit_amount=Decimal("1.00"), reservation_amount=Decimal("0.25"),
        )
        assert budget.reserve(
            "org-a", "budget://org-a/chat", "provider-a", "fast_chat", "trace-unknown", call_id="call-unknown"
        ) is True
        budget.mark_unknown("org-a", "budget://org-a/chat", "call-unknown")

        account = budget.get_account("org-a", "budget://org-a/chat")
        reservation = budget.get_reservation("org-a", "call-unknown")
        assert reservation.status == "RECONCILIATION_REQUIRED"
        assert account.reserved_amount == Decimal("0.25000000")
    finally:
        engine.dispose()


def test_budget_currency_must_match_litellm_cost_unit():
    engine, budget = _budget_with_schema()
    try:
        try:
            budget.configure_account(
                "org-a", "budget://org-a/euro", currency="EUR",
                limit_amount=Decimal("1.00"), reservation_amount=Decimal("0.25"),
            )
        except ValueError as error:
            assert "currency" in str(error)
        else:
            raise AssertionError("costs without currency conversion must not use a non-USD account")
    finally:
        engine.dispose()
