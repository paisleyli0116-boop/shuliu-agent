from __future__ import annotations

from datetime import UTC, datetime

from shuliu_runtime.tools.idempotency import (
    IdempotencyClaim,
    IdempotencyClaimStatus,
    decode_idempotency_result,
    encode_idempotency_result,
    validate_idempotency_values,
)

from .sqlite import SQLiteDatabase


class SQLiteIdempotencyStore:
    """Durable idempotency for device-local task state only."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def begin(
        self, *, scope: tuple[str, ...], key: str, request_digest: str
    ) -> IdempotencyClaim:
        _validate_call(scope, key, request_digest)
        organization_id, tool_id, operation_id = _scope_values(scope)
        now = _now()
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT request_digest, status, result_json FROM tool_idempotency "
                "WHERE organization_id = ? AND tool_id = ? "
                "AND operation_id = ? AND idempotency_key = ?",
                (organization_id, tool_id, operation_id, key),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO tool_idempotency "
                    "(organization_id, tool_id, operation_id, "
                    "idempotency_key, request_digest, status, result_json, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'CLAIMED', "
                    "NULL, ?, ?)",
                    (
                        organization_id,
                        tool_id,
                        operation_id,
                        key,
                        request_digest,
                        now,
                        now,
                    ),
                )
                return IdempotencyClaim(IdempotencyClaimStatus.NEW)
            if row["request_digest"] != request_digest:
                return IdempotencyClaim(IdempotencyClaimStatus.CONFLICT)
            status = row["status"]
            if status == "CLAIMED":
                return IdempotencyClaim(IdempotencyClaimStatus.IN_PROGRESS)
            if status in {"DISPATCHED", "INDETERMINATE"}:
                return IdempotencyClaim(
                    IdempotencyClaimStatus.RECONCILIATION_REQUIRED
                )
            if status == "COMPLETED" and row["result_json"] is not None:
                return IdempotencyClaim(
                    IdempotencyClaimStatus.COMPLETED,
                    decode_idempotency_result(row["result_json"]),
                )
            raise ValueError("idempotency state is invalid")

    def mark_dispatched(
        self, *, scope: tuple[str, ...], key: str, request_digest: str
    ) -> None:
        _validate_call(scope, key, request_digest)
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
        _validate_call(scope, key, request_digest)
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
        _validate_call(scope, key, request_digest)
        organization_id, tool_id, operation_id = _scope_values(scope)
        result_json = encode_idempotency_result(result)
        now = _now()
        with self._database.connect() as connection:
            cursor = connection.execute(
                "UPDATE tool_idempotency SET status = 'COMPLETED', result_json = ?, "
                "updated_at = ? WHERE organization_id = ? "
                "AND tool_id = ? AND operation_id = ? AND idempotency_key = ? "
                "AND request_digest = ? AND status = 'DISPATCHED'",
                (
                    result_json,
                    now,
                    organization_id,
                    tool_id,
                    operation_id,
                    key,
                    request_digest,
                ),
            )
            if cursor.rowcount == 1:
                return
            current = connection.execute(
                "SELECT request_digest, status, result_json FROM tool_idempotency "
                "WHERE organization_id = ? AND tool_id = ? "
                "AND operation_id = ? AND idempotency_key = ?",
                (organization_id, tool_id, operation_id, key),
            ).fetchone()
            if (
                current is None
                or current["request_digest"] != request_digest
                or current["status"] != "COMPLETED"
                or current["result_json"] != result_json
            ):
                raise ValueError("idempotency completion transition rejected")

    def release_before_dispatch(
        self, *, scope: tuple[str, ...], key: str, request_digest: str
    ) -> None:
        _validate_call(scope, key, request_digest)
        organization_id, tool_id, operation_id = _scope_values(scope)
        with self._database.connect() as connection:
            connection.execute(
                "DELETE FROM tool_idempotency WHERE organization_id = ? "
                "AND tool_id = ? AND operation_id = ? "
                "AND idempotency_key = ? AND request_digest = ? AND status = 'CLAIMED'",
                (organization_id, tool_id, operation_id, key, request_digest),
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
        organization_id, tool_id, operation_id = _scope_values(scope)
        now = _now()
        placeholders = ", ".join("?" for _ in expected)
        with self._database.connect() as connection:
            cursor = connection.execute(
                f"UPDATE tool_idempotency SET status = ?, updated_at = ? "
                f"WHERE organization_id = ? AND tool_id = ? "
                f"AND operation_id = ? AND idempotency_key = ? "
                f"AND request_digest = ? AND status IN ({placeholders})",
                (
                    target,
                    now,
                    organization_id,
                    tool_id,
                    operation_id,
                    key,
                    request_digest,
                    *expected,
                ),
            )
            if cursor.rowcount == 1:
                return
            current = connection.execute(
                "SELECT request_digest, status FROM tool_idempotency "
                "WHERE organization_id = ? AND tool_id = ? "
                "AND operation_id = ? AND idempotency_key = ?",
                (organization_id, tool_id, operation_id, key),
            ).fetchone()
            if (
                current is not None
                and current["request_digest"] == request_digest
                and current["status"] in idempotent
            ):
                return
            raise ValueError("idempotency state transition rejected")


def _scope_values(scope: tuple[str, ...]) -> tuple[str, str, str]:
    if (
        not isinstance(scope, tuple)
        or len(scope) != 3
        or any(not isinstance(value, str) or not value.strip() for value in scope)
        or any(len(value) > 128 for value in scope)
    ):
        raise ValueError("durable idempotency scope must contain three values of at most 128 characters")
    return scope  # type: ignore[return-value]


def _validate_call(scope: tuple[str, ...], key: str, request_digest: str) -> None:
    validate_idempotency_values(
        scope, key, request_digest, expected_scope_size=3
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
