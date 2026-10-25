from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    MetaData,
    Numeric,
    String,
    Table,
    and_,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from .usage import validate_usage


_metadata = MetaData()
_MONEY_QUANTUM = Decimal("0.00000001")

_model_calls = Table(
    "model_calls",
    _metadata,
    Column("call_id", String(128), primary_key=True),
    Column("organization_id", String(128), primary_key=True),
    Column("trace_id", String(128), nullable=False),
    Column("requested_capability", String(64), nullable=False),
    Column("provider_id", String(128), nullable=False),
    Column("model", String(256), nullable=False),
    Column("model_version", String(128), nullable=False),
    Column("usage", JSON, nullable=False),
    Column("latency_ms", Numeric(14, 3), nullable=False),
    Column("cost", Numeric(20, 8)),
    Column("currency", String(3), nullable=False),
    Column("error_code", String(96)),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("latency_ms >= 0", name="ck_model_calls_latency_nonnegative"),
    CheckConstraint("cost IS NULL OR cost >= 0", name="ck_model_calls_cost_nonnegative"),
    CheckConstraint(
        "status IN ('SUCCEEDED', 'FAILED', 'UNKNOWN')",
        name="ck_model_calls_status",
    ),
)

_budget_accounts = Table(
    "model_budget_accounts",
    _metadata,
    Column("organization_id", String(128), primary_key=True),
    Column("budget_ref", String(256), primary_key=True),
    Column("currency", String(3), nullable=False),
    Column("limit_amount", Numeric(20, 8), nullable=False),
    Column("reservation_amount", Numeric(20, 8), nullable=False),
    Column("reserved_amount", Numeric(20, 8), nullable=False),
    Column("spent_amount", Numeric(20, 8), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("limit_amount >= 0", name="ck_model_budget_limit_nonnegative"),
    CheckConstraint("reservation_amount > 0", name="ck_model_budget_reservation_positive"),
    CheckConstraint("reserved_amount >= 0", name="ck_model_budget_reserved_nonnegative"),
    CheckConstraint("spent_amount >= 0", name="ck_model_budget_spent_nonnegative"),
)

_budget_reservations = Table(
    "model_budget_reservations",
    _metadata,
    Column("call_id", String(128), primary_key=True),
    Column("organization_id", String(128), primary_key=True),
    Column("budget_ref", String(256), nullable=False),
    Column("provider_id", String(128), nullable=False),
    Column("capability", String(64), nullable=False),
    Column("trace_id", String(128), nullable=False),
    Column("reserved_amount", Numeric(20, 8), nullable=False),
    Column("settled_amount", Numeric(20, 8)),
    Column("usage", JSON),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["organization_id", "budget_ref"],
        ["model_budget_accounts.organization_id", "model_budget_accounts.budget_ref"],
        name="fk_model_budget_reservation_account",
    ),
    CheckConstraint("reserved_amount > 0", name="ck_model_budget_reservation_amount_positive"),
    CheckConstraint("settled_amount IS NULL OR settled_amount >= 0", name="ck_model_budget_settled_nonnegative"),
    CheckConstraint(
        "status IN ('RESERVED', 'RECONCILIATION_REQUIRED', 'SETTLED', 'RELEASED')",
        name="ck_model_budget_reservation_status",
    ),
)


class ModelUsageConflictError(RuntimeError):
    """A durable call or reservation identity was reused with different data."""


class ModelBudgetAccountMissingError(RuntimeError):
    """A budget reference has no configured durable account."""


@dataclass(frozen=True)
class PersistentModelCall:
    call_id: str
    organization_id: str
    trace_id: str
    requested_capability: str
    provider_id: str
    model: str
    model_version: str
    usage: dict[str, int]
    latency_ms: Decimal
    cost: Decimal | None
    currency: str
    error_code: str | None
    status: str
    created_at: datetime


@dataclass(frozen=True)
class PersistentBudgetAccount:
    organization_id: str
    budget_ref: str
    currency: str
    limit_amount: Decimal
    reservation_amount: Decimal
    reserved_amount: Decimal
    spent_amount: Decimal


@dataclass(frozen=True)
class PersistentBudgetReservation:
    call_id: str
    organization_id: str
    budget_ref: str
    provider_id: str
    capability: str
    trace_id: str
    reserved_amount: Decimal
    settled_amount: Decimal | None
    usage: dict[str, int] | None
    status: str


class PostgresModelCallSink:
    """Durable, organization-scoped ModelCall sink. Payload and Secret data are excluded."""

    def __init__(self, engine: Engine):
        self._engine = engine

    def record(self, call: Any) -> None:
        values = _call_values(call)
        with self._engine.begin() as connection:
            _record_model_call(connection, values)

    def get(self, organization_id: str, call_id: str) -> PersistentModelCall | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(_model_calls).where(
                    and_(
                        _model_calls.c.organization_id == organization_id,
                        _model_calls.c.call_id == call_id,
                    )
                )
            ).mappings().first()
        if row is None:
            return None
        return PersistentModelCall(
            call_id=row["call_id"],
            organization_id=row["organization_id"],
            trace_id=row["trace_id"],
            requested_capability=row["requested_capability"],
            provider_id=row["provider_id"],
            model=row["model"],
            model_version=row["model_version"],
            usage=dict(row["usage"]),
            latency_ms=Decimal(row["latency_ms"]),
            cost=Decimal(row["cost"]) if row["cost"] is not None else None,
            currency=row["currency"],
            error_code=row["error_code"],
            status=row["status"],
            created_at=_aware_utc(row["created_at"]),
        )


class PostgresModelBudget:
    """Atomic organization-scoped budget accounting with durable reservations."""

    def __init__(self, engine: Engine):
        self._engine = engine

    def configure_account(
        self,
        organization_id: str,
        budget_ref: str,
        *,
        currency: str,
        limit_amount: Decimal,
        reservation_amount: Decimal,
    ) -> PersistentBudgetAccount:
        organization_id = _required_text(organization_id, "organization_id")
        budget_ref = _required_text(budget_ref, "budget_ref")
        if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise ValueError("currency must be a three-letter code")
        if currency.upper() != "USD":
            raise ValueError("model budget currency must match the Provider cost unit (USD)")
        limit_value = _money(limit_amount, allow_zero=True)
        reservation_value = _money(reservation_amount)
        if reservation_value <= 0 or reservation_value > limit_value:
            raise ValueError("reservation amount must be positive and within the budget limit")
        now = datetime.now(timezone.utc)
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(_budget_accounts).where(
                    and_(
                        _budget_accounts.c.organization_id == organization_id,
                        _budget_accounts.c.budget_ref == budget_ref,
                    )
                )
            ).mappings().first()
            if existing is None:
                connection.execute(
                    insert(_budget_accounts).values(
                        organization_id=organization_id,
                        budget_ref=budget_ref,
                        currency=currency.upper(),
                        limit_amount=limit_value,
                        reservation_amount=reservation_value,
                        reserved_amount=Decimal("0"),
                        spent_amount=Decimal("0"),
                        updated_at=now,
                    )
                )
            elif (
                existing["currency"] != currency.upper()
                or Decimal(existing["limit_amount"]) != limit_value
                or Decimal(existing["reservation_amount"]) != reservation_value
            ):
                raise ModelUsageConflictError("budget account configuration conflicts with existing account")
        account = self.get_account(organization_id, budget_ref)
        assert account is not None
        return account

    def reserve(
        self,
        organization_id: str,
        budget_ref: str,
        provider_id: str,
        capability: str,
        trace_id: str,
        *,
        call_id: str,
    ) -> bool:
        organization_id = _required_text(organization_id, "organization_id")
        budget_ref = _required_text(budget_ref, "budget_ref")
        call_id = _required_text(call_id, "call_id")
        now = datetime.now(timezone.utc)
        with self._engine.begin() as connection:
            account = connection.execute(
                select(_budget_accounts)
                .where(
                    and_(
                        _budget_accounts.c.organization_id == organization_id,
                        _budget_accounts.c.budget_ref == budget_ref,
                    )
                )
                .with_for_update()
            ).mappings().first()
            if account is None:
                raise ModelBudgetAccountMissingError("durable model budget account is not configured")
            existing = connection.execute(
                select(_budget_reservations).where(
                    and_(
                        _budget_reservations.c.organization_id == organization_id,
                        _budget_reservations.c.call_id == call_id,
                    )
                )
            ).mappings().first()
            if existing is not None:
                if (
                    existing["budget_ref"] != budget_ref
                    or existing["provider_id"] != provider_id
                    or existing["capability"] != capability
                    or existing["trace_id"] != trace_id
                ):
                    raise ModelUsageConflictError("model call reservation identity conflicts")
                return False
            held = Decimal(account["reserved_amount"])
            spent = Decimal(account["spent_amount"])
            limit = Decimal(account["limit_amount"])
            amount = Decimal(account["reservation_amount"])
            if spent + held + amount > limit:
                return False
            connection.execute(
                insert(_budget_reservations).values(
                    call_id=call_id,
                    organization_id=organization_id,
                    budget_ref=budget_ref,
                    provider_id=_required_text(provider_id, "provider_id"),
                    capability=_required_text(capability, "capability"),
                    trace_id=_required_text(trace_id, "trace_id"),
                    reserved_amount=amount,
                    settled_amount=None,
                    usage=None,
                    status="RESERVED",
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                update(_budget_accounts)
                .where(
                    and_(
                        _budget_accounts.c.organization_id == organization_id,
                        _budget_accounts.c.budget_ref == budget_ref,
                    )
                )
                .values(reserved_amount=held + amount, updated_at=now)
            )
        return True

    def settle(
        self,
        organization_id: str,
        budget_ref: str,
        call_id: str,
        usage: Mapping[str, int],
        cost: Decimal | int | float,
    ) -> None:
        usage_value = _validated_usage(usage)
        cost_value = _money(cost, allow_zero=True)
        now = datetime.now(timezone.utc)
        with self._engine.begin() as connection:
            reservation = self._reservation_for_update(connection, organization_id, budget_ref, call_id)
            if reservation["status"] == "SETTLED":
                if (
                    Decimal(reservation["settled_amount"]) != cost_value
                    or dict(reservation["usage"]) != usage_value
                ):
                    raise ModelUsageConflictError("model budget settlement conflicts with prior settlement")
                return
            if reservation["status"] == "RELEASED":
                raise ModelUsageConflictError("released model budget reservation cannot be settled")
            account = self._account_for_update(connection, organization_id, budget_ref)
            connection.execute(
                update(_budget_accounts)
                .where(
                    and_(
                        _budget_accounts.c.organization_id == organization_id,
                        _budget_accounts.c.budget_ref == budget_ref,
                    )
                )
                .values(
                    reserved_amount=Decimal(account["reserved_amount"]) - Decimal(reservation["reserved_amount"]),
                    spent_amount=Decimal(account["spent_amount"]) + cost_value,
                    updated_at=now,
                )
            )
            connection.execute(
                update(_budget_reservations)
                .where(
                    and_(
                        _budget_reservations.c.organization_id == organization_id,
                        _budget_reservations.c.call_id == call_id,
                    )
                )
                .values(settled_amount=cost_value, usage=usage_value, status="SETTLED", updated_at=now)
            )

    def finalize_call(self, call: Any, *, budget_ref: str) -> None:
        """Atomically finalize a reservation and persist its ModelCall record."""
        values = _call_values(call)
        budget_ref = _required_text(budget_ref, "budget_ref")
        if values["status"] == "SUCCEEDED":
            target_status = "SETTLED"
        elif values["status"] == "FAILED":
            target_status = "RELEASED"
        else:
            target_status = "RECONCILIATION_REQUIRED"
        now = datetime.now(timezone.utc)
        with self._engine.begin() as connection:
            reservation = self._reservation_for_update(
                connection,
                values["organization_id"],
                budget_ref,
                values["call_id"],
            )
            if (
                reservation["provider_id"] != values["provider_id"]
                or reservation["capability"] != values["requested_capability"]
                or reservation["trace_id"] != values["trace_id"]
            ):
                raise ModelUsageConflictError("model call does not match its budget reservation")

            current_status = reservation["status"]
            if current_status == target_status:
                if target_status == "SETTLED" and (
                    Decimal(reservation["settled_amount"]) != values["cost"]
                    or dict(reservation["usage"]) != values["usage"]
                ):
                    raise ModelUsageConflictError("model budget settlement conflicts with prior settlement")
                _record_model_call(connection, values)
                return

            if target_status == "SETTLED":
                if current_status not in {"RESERVED", "RECONCILIATION_REQUIRED"}:
                    raise ModelUsageConflictError("model budget reservation cannot be settled")
                if values["cost"] is None:
                    raise ValueError("successful model calls require a recorded cost")
                account = self._account_for_update(
                    connection, values["organization_id"], budget_ref
                )
                reserved_after = Decimal(account["reserved_amount"]) - Decimal(reservation["reserved_amount"])
                if reserved_after < 0:
                    raise ModelUsageConflictError("model budget reservation accounting is inconsistent")
                connection.execute(
                    update(_budget_accounts)
                    .where(
                        and_(
                            _budget_accounts.c.organization_id == values["organization_id"],
                            _budget_accounts.c.budget_ref == budget_ref,
                        )
                    )
                    .values(
                        reserved_amount=reserved_after,
                        spent_amount=Decimal(account["spent_amount"]) + values["cost"],
                        updated_at=now,
                    )
                )
                connection.execute(
                    update(_budget_reservations)
                    .where(
                        and_(
                            _budget_reservations.c.organization_id == values["organization_id"],
                            _budget_reservations.c.call_id == values["call_id"],
                        )
                    )
                    .values(
                        settled_amount=values["cost"],
                        usage=values["usage"],
                        status="SETTLED",
                        updated_at=now,
                    )
                )
            elif target_status == "RELEASED":
                if current_status != "RESERVED":
                    raise ModelUsageConflictError("model budget reservation cannot be released")
                account = self._account_for_update(
                    connection, values["organization_id"], budget_ref
                )
                reserved_after = Decimal(account["reserved_amount"]) - Decimal(reservation["reserved_amount"])
                if reserved_after < 0:
                    raise ModelUsageConflictError("model budget reservation accounting is inconsistent")
                connection.execute(
                    update(_budget_accounts)
                    .where(
                        and_(
                            _budget_accounts.c.organization_id == values["organization_id"],
                            _budget_accounts.c.budget_ref == budget_ref,
                        )
                    )
                    .values(reserved_amount=reserved_after, updated_at=now)
                )
                connection.execute(
                    update(_budget_reservations)
                    .where(
                        and_(
                            _budget_reservations.c.organization_id == values["organization_id"],
                            _budget_reservations.c.call_id == values["call_id"],
                        )
                    )
                    .values(status="RELEASED", updated_at=now)
                )
            else:
                if current_status != "RESERVED":
                    raise ModelUsageConflictError("model budget reservation requires reconciliation")
                connection.execute(
                    update(_budget_reservations)
                    .where(
                        and_(
                            _budget_reservations.c.organization_id == values["organization_id"],
                            _budget_reservations.c.call_id == values["call_id"],
                        )
                    )
                    .values(status="RECONCILIATION_REQUIRED", updated_at=now)
                )

            _record_model_call(connection, values)

    def release(self, organization_id: str, budget_ref: str, call_id: str) -> None:
        now = datetime.now(timezone.utc)
        with self._engine.begin() as connection:
            reservation = self._reservation_for_update(connection, organization_id, budget_ref, call_id)
            if reservation["status"] == "RELEASED":
                return
            if reservation["status"] != "RESERVED":
                raise ModelUsageConflictError("only a known, unresolved reservation can be released")
            account = self._account_for_update(connection, organization_id, budget_ref)
            connection.execute(
                update(_budget_accounts)
                .where(
                    and_(
                        _budget_accounts.c.organization_id == organization_id,
                        _budget_accounts.c.budget_ref == budget_ref,
                    )
                )
                .values(
                    reserved_amount=Decimal(account["reserved_amount"]) - Decimal(reservation["reserved_amount"]),
                    updated_at=now,
                )
            )
            connection.execute(
                update(_budget_reservations)
                .where(
                    and_(
                        _budget_reservations.c.organization_id == organization_id,
                        _budget_reservations.c.call_id == call_id,
                    )
                )
                .values(status="RELEASED", updated_at=now)
            )

    def mark_unknown(self, organization_id: str, budget_ref: str, call_id: str) -> None:
        reservation = self.get_reservation(organization_id, call_id)
        if reservation is None or reservation.budget_ref != budget_ref:
            raise ModelUsageConflictError("model budget reservation is not available for reconciliation")
        if reservation.status == "RECONCILIATION_REQUIRED":
            return
        if reservation.status != "RESERVED":
            raise ModelUsageConflictError("model budget reservation is already finalized")
        with self._engine.begin() as connection:
            connection.execute(
                update(_budget_reservations)
                .where(
                    and_(
                        _budget_reservations.c.organization_id == organization_id,
                        _budget_reservations.c.call_id == call_id,
                        _budget_reservations.c.status == "RESERVED",
                    )
                )
                .values(status="RECONCILIATION_REQUIRED", updated_at=datetime.now(timezone.utc))
            )

    def get_account(self, organization_id: str, budget_ref: str) -> PersistentBudgetAccount | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(_budget_accounts).where(
                    and_(
                        _budget_accounts.c.organization_id == organization_id,
                        _budget_accounts.c.budget_ref == budget_ref,
                    )
                )
            ).mappings().first()
        if row is None:
            return None
        return PersistentBudgetAccount(
            organization_id=row["organization_id"],
            budget_ref=row["budget_ref"],
            currency=row["currency"],
            limit_amount=Decimal(row["limit_amount"]),
            reservation_amount=Decimal(row["reservation_amount"]),
            reserved_amount=Decimal(row["reserved_amount"]),
            spent_amount=Decimal(row["spent_amount"]),
        )

    def get_reservation(self, organization_id: str, call_id: str) -> PersistentBudgetReservation | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(_budget_reservations).where(
                    and_(
                        _budget_reservations.c.organization_id == organization_id,
                        _budget_reservations.c.call_id == call_id,
                    )
                )
            ).mappings().first()
        if row is None:
            return None
        return PersistentBudgetReservation(
            call_id=row["call_id"],
            organization_id=row["organization_id"],
            budget_ref=row["budget_ref"],
            provider_id=row["provider_id"],
            capability=row["capability"],
            trace_id=row["trace_id"],
            reserved_amount=Decimal(row["reserved_amount"]),
            settled_amount=Decimal(row["settled_amount"]) if row["settled_amount"] is not None else None,
            usage=dict(row["usage"]) if row["usage"] is not None else None,
            status=row["status"],
        )

    def _reservation_for_update(self, connection, organization_id: str, budget_ref: str, call_id: str):
        row = connection.execute(
            select(_budget_reservations)
            .where(
                and_(
                    _budget_reservations.c.organization_id == organization_id,
                    _budget_reservations.c.budget_ref == budget_ref,
                    _budget_reservations.c.call_id == call_id,
                )
            )
            .with_for_update()
        ).mappings().first()
        if row is None:
            raise ModelUsageConflictError("model budget reservation does not exist")
        return row

    @staticmethod
    def _account_for_update(connection, organization_id: str, budget_ref: str):
        row = connection.execute(
            select(_budget_accounts)
            .where(
                and_(
                    _budget_accounts.c.organization_id == organization_id,
                    _budget_accounts.c.budget_ref == budget_ref,
                )
            )
            .with_for_update()
        ).mappings().first()
        if row is None:
            raise ModelBudgetAccountMissingError("durable model budget account is not configured")
        return row


def _call_values(call: Any) -> dict[str, Any]:
    values = {
        "call_id": _required_text(call.call_id, "call_id"),
        "organization_id": _required_text(call.organization_id, "organization_id"),
        "trace_id": _required_text(call.trace_id, "trace_id"),
        "requested_capability": _required_text(call.requested_capability, "requested_capability"),
        "provider_id": _required_text(call.resolved_provider, "provider_id"),
        "model": _required_text(call.resolved_model, "model"),
        "model_version": _required_text(call.model_version, "model_version"),
        "usage": _validated_usage(call.usage),
        "latency_ms": _latency(call.latency_ms),
        "cost": _money(call.cost, allow_zero=True) if call.cost is not None else None,
        "currency": "USD",
        "error_code": call.error_code,
        "status": call.status,
    }
    if values["status"] not in {"SUCCEEDED", "FAILED", "UNKNOWN"}:
        raise ValueError("model call status is invalid")
    if values["error_code"] is not None:
        values["error_code"] = _required_text(values["error_code"], "error_code")
    return values


def _ensure_same_call(existing: Mapping[str, Any], values: Mapping[str, Any]) -> None:
    if any(existing[key] != values[key] for key in values):
        raise ModelUsageConflictError("model call identity conflicts with existing record")


def _record_model_call(connection, values: Mapping[str, Any]) -> None:
    created_at = datetime.now(timezone.utc)
    dialect_insert = {
        "postgresql": postgresql_insert,
        "sqlite": sqlite_insert,
    }.get(connection.dialect.name)
    if dialect_insert is not None:
        result = connection.execute(
            dialect_insert(_model_calls)
            .values(**values, created_at=created_at)
            .on_conflict_do_nothing(
                index_elements=[_model_calls.c.call_id, _model_calls.c.organization_id]
            )
        )
        if result.rowcount:
            return
    else:
        existing = _find_model_call(connection, values)
        if existing is None:
            connection.execute(insert(_model_calls).values(**values, created_at=created_at))
            return

    existing = _find_model_call(connection, values)
    if existing is None:
        raise ModelUsageConflictError("model call identity could not be verified after insert")
    _ensure_same_call(existing, values)


def _find_model_call(connection, values: Mapping[str, Any]):
    return connection.execute(
        select(_model_calls).where(
            and_(
                _model_calls.c.organization_id == values["organization_id"],
                _model_calls.c.call_id == values["call_id"],
            )
        )
    ).mappings().first()


def _money(value: Any, *, allow_zero: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float)):
        raise ValueError("monetary amount is invalid")
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or (not allow_zero and result == 0):
            raise ValueError("monetary amount is invalid")
        return result.quantize(_MONEY_QUANTUM)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("monetary amount is invalid") from error


def _latency(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("model call latency is invalid")
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("model call latency is invalid")
    return result.quantize(Decimal("0.001"))


def _validated_usage(value: Mapping[str, Any]) -> dict[str, int]:
    return validate_usage(value)


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
