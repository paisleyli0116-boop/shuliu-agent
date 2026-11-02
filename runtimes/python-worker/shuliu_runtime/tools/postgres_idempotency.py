from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from .idempotency import (
    IdempotencyClaim,
    IdempotencyClaimStatus,
    IdempotencyRecord,
    IdempotencyRecordStatus,
    decode_idempotency_result,
    encode_idempotency_result,
    validate_idempotency_values,
)


_metadata = MetaData()
tool_idempotency = Table(
    "tool_idempotency",
    _metadata,
    Column("organization_id", String(128), nullable=False),
    Column("tool_id", String(128), nullable=False),
    Column("operation_id", String(128), nullable=False),
    Column("idempotency_key", String(256), nullable=False),
    Column("request_digest", String(128), nullable=False),
    Column("status", String(32), nullable=False),
    # none_as_null keeps a Python None as SQL NULL so the completed-result check
    # constraint can distinguish "no result yet" from a stored JSON null.
    Column("result_json", JSON(none_as_null=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "status IN ('CLAIMED', 'DISPATCHED', 'COMPLETED', 'INDETERMINATE')",
        name="ck_tool_idempotency_status",
    ),
    CheckConstraint(
        "length(request_digest) BETWEEN 1 AND 128",
        name="ck_tool_idempotency_digest_length",
    ),
    CheckConstraint(
        "length(organization_id) BETWEEN 1 AND 128 AND "
        "length(tool_id) BETWEEN 1 AND 128 AND "
        "length(operation_id) BETWEEN 1 AND 128",
        name="ck_tool_idempotency_scope_length",
    ),
    CheckConstraint(
        "length(idempotency_key) BETWEEN 1 AND 256",
        name="ck_tool_idempotency_key_length",
    ),
    CheckConstraint(
        "(status = 'COMPLETED' AND result_json IS NOT NULL) OR "
        "(status <> 'COMPLETED' AND result_json IS NULL)",
        name="ck_tool_idempotency_completed_result",
    ),
    UniqueConstraint(
        "organization_id",
        "tool_id",
        "operation_id",
        "idempotency_key",
        name="uq_tool_idempotency_scope_key",
    ),
)
Index(
    "ix_tool_idempotency_status_updated",
    tool_idempotency.c.status,
    tool_idempotency.c.updated_at,
)

_SCOPE_COLUMNS = (
    tool_idempotency.c.organization_id,
    tool_idempotency.c.tool_id,
    tool_idempotency.c.operation_id,
)


class PostgresIdempotencyStore:
    """Shared Cloud/Worker idempotency state using transactional PostgreSQL rows."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def begin(
        self, *, scope: tuple[str, ...], key: str, request_digest: str
    ) -> IdempotencyClaim:
        values = _row_scope(scope, key, request_digest)
        now = _now()
        statement = (
            pg_insert(tool_idempotency)
            .values(**values, status="CLAIMED", result_json=None, created_at=now, updated_at=now)
            .on_conflict_do_nothing(index_elements=list(_SCOPE_COLUMNS) + [tool_idempotency.c.idempotency_key])
            .returning(tool_idempotency.c.status)
        )
        with self._engine.begin() as connection:
            inserted = connection.execute(statement).scalar_one_or_none()
            if inserted is not None:
                return IdempotencyClaim(IdempotencyClaimStatus.NEW)
            row = connection.execute(
                select(tool_idempotency)
                .where(*_scope_predicates(values, key))
                .with_for_update()
            ).mappings().first()
            if row is None:
                raise RuntimeError("idempotency claim could not be loaded")
            return _claim_for_row(row, request_digest)

    def inspect(
        self, *, scope: tuple[str, ...], key: str, request_digest: str
    ) -> IdempotencyRecord:
        """Read durable claim state without locking or mutating the claim."""
        values = _row_scope(scope, key, request_digest)
        with self._engine.connect() as connection:
            row = connection.execute(
                select(tool_idempotency).where(*_scope_predicates(values, key))
            ).mappings().first()
        if row is None:
            return IdempotencyRecord(IdempotencyRecordStatus.ABSENT)
        if row["request_digest"] != request_digest:
            return IdempotencyRecord(IdempotencyRecordStatus.CONFLICT)
        if row["status"] == "COMPLETED" and row["result_json"] is not None:
            encoded = json.dumps(
                row["result_json"], ensure_ascii=False, separators=(",", ":")
            )
            return IdempotencyRecord(
                IdempotencyRecordStatus.COMPLETED,
                decode_idempotency_result(encoded),
            )
        statuses = {
            "CLAIMED": IdempotencyRecordStatus.CLAIMED,
            "DISPATCHED": IdempotencyRecordStatus.DISPATCHED,
            "INDETERMINATE": IdempotencyRecordStatus.INDETERMINATE,
        }
        status = statuses.get(row["status"])
        if status is None or row["result_json"] is not None:
            raise ValueError("idempotency record state is invalid")
        return IdempotencyRecord(status)

    def mark_dispatched(
        self, *, scope: tuple[str, ...], key: str, request_digest: str
    ) -> None:
        self._transition(
            scope,
            key,
            request_digest,
            expected=("CLAIMED",),
            target="DISPATCHED",
            idempotent=("DISPATCHED",),
        )

    def mark_indeterminate(
        self, *, scope: tuple[str, ...], key: str, request_digest: str
    ) -> None:
        self._transition(
            scope,
            key,
            request_digest,
            expected=("CLAIMED", "DISPATCHED"),
            target="INDETERMINATE",
            idempotent=("INDETERMINATE", "COMPLETED"),
        )

    def complete(
        self,
        *,
        scope: tuple[str, ...],
        key: str,
        request_digest: str,
        result: object,
    ) -> None:
        values = _row_scope(scope, key, request_digest)
        result_value = json.loads(encode_idempotency_result(result))
        with self._engine.begin() as connection:
            cursor = connection.execute(
                update(tool_idempotency)
                .where(
                    *_scope_predicates(values, key),
                    tool_idempotency.c.request_digest == request_digest,
                    tool_idempotency.c.status == "DISPATCHED",
                )
                .values(status="COMPLETED", result_json=result_value, updated_at=_now())
            )
            if cursor.rowcount == 1:
                return
            row = connection.execute(
                select(tool_idempotency.c.request_digest, tool_idempotency.c.status, tool_idempotency.c.result_json)
                .where(*_scope_predicates(values, key))
            ).mappings().first()
            if (
                row is None
                or row["request_digest"] != request_digest
                or row["status"] != "COMPLETED"
                or row["result_json"] != result_value
            ):
                raise ValueError("idempotency completion transition rejected")

    def release_before_dispatch(
        self, *, scope: tuple[str, ...], key: str, request_digest: str
    ) -> None:
        values = _row_scope(scope, key, request_digest)
        with self._engine.begin() as connection:
            connection.execute(
                tool_idempotency.delete().where(
                    *_scope_predicates(values, key),
                    tool_idempotency.c.request_digest == request_digest,
                    tool_idempotency.c.status == "CLAIMED",
                )
            )

    def _transition(
        self,
        scope: tuple[str, ...],
        key: str,
        request_digest: str,
        *,
        expected: tuple[str, ...],
        target: str,
        idempotent: tuple[str, ...],
    ) -> None:
        values = _row_scope(scope, key, request_digest)
        with self._engine.begin() as connection:
            cursor = connection.execute(
                update(tool_idempotency)
                .where(
                    *_scope_predicates(values, key),
                    tool_idempotency.c.request_digest == request_digest,
                    tool_idempotency.c.status.in_(expected),
                )
                .values(status=target, updated_at=_now())
            )
            if cursor.rowcount == 1:
                return
            row = connection.execute(
                select(tool_idempotency.c.request_digest, tool_idempotency.c.status)
                .where(*_scope_predicates(values, key))
            ).mappings().first()
            if (
                row is not None
                and row["request_digest"] == request_digest
                and row["status"] in idempotent
            ):
                return
            raise ValueError("idempotency state transition rejected")


def _row_scope(
    scope: tuple[str, ...], key: str, request_digest: str
) -> dict[str, str]:
    validate_idempotency_values(
        scope, key, request_digest, expected_scope_size=3
    )
    names = ("organization_id", "tool_id", "operation_id")
    if any(len(value) > 128 for value in scope):
        raise ValueError("durable idempotency scope values must be at most 128 characters")
    return dict(zip(names, scope, strict=True)) | {
        "idempotency_key": key,
        "request_digest": request_digest,
    }


def _scope_predicates(values: dict[str, str], key: str) -> tuple[object, ...]:
    return tuple(column == values[column.name] for column in _SCOPE_COLUMNS) + (
        tool_idempotency.c.idempotency_key == key,
    )


def _claim_for_row(row: Mapping[str, Any], request_digest: str) -> IdempotencyClaim:
    if row["request_digest"] != request_digest:
        return IdempotencyClaim(IdempotencyClaimStatus.CONFLICT)
    status = row["status"]
    if status == "CLAIMED":
        return IdempotencyClaim(IdempotencyClaimStatus.IN_PROGRESS)
    if status in {"DISPATCHED", "INDETERMINATE"}:
        return IdempotencyClaim(IdempotencyClaimStatus.RECONCILIATION_REQUIRED)
    if status == "COMPLETED" and row["result_json"] is not None:
        encoded = json.dumps(row["result_json"], ensure_ascii=False, separators=(",", ":"))
        return IdempotencyClaim(
            IdempotencyClaimStatus.COMPLETED, decode_idempotency_result(encoded)
        )
    raise ValueError("idempotency state is invalid")


def _now() -> datetime:
    return datetime.now(UTC)
