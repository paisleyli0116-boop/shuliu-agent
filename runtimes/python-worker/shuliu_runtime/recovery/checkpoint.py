from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from shuliu_runtime.events.models import EventRecord, event_to_dict
from shuliu_runtime.persistence.outbox import canonical_json
from shuliu_runtime.persistence.sqlite import SQLiteDatabase


CHECKPOINT_SCHEMA_VERSION = 1
_MAX_SEQUENCE = 2**63 - 1
_SAFE_RECOVERY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_PROCESS_RECOVERY_STATUSES = frozenset({"RESUME", "WAIT", "SAFE_MODE", "DEAD_LETTER"})
_PROCESS_RECOVERY_ERROR_CODES = frozenset(
    {
        "APPLIED_RESULT_UNAVAILABLE",
        "APPROVAL_PENDING",
        "CHECKPOINT_NOT_FOUND",
        "CHECKPOINT_SCHEMA_INCOMPATIBLE",
        "CHECKPOINT_STATE_INVALID",
        "CHECKPOINT_UNAVAILABLE",
        "EXTERNAL_STATE_INVALID",
        "EXTERNAL_STATE_UNAVAILABLE",
        "IDEMPOTENCY_KEY_REQUIRED",
        "PENDING_ACTION_INVALID",
        "PROCESS_RECOVERY_FAILED",
        "RECONCILED_RESULT_INVALID",
        "RECOVERY_AUDIT_UNAVAILABLE",
        "RECOVERY_METADATA_INVALID",
        "RUNTIME_GENERATION_INVALID",
        "SIDE_EFFECT_STATE_UNKNOWN",
        "WORKER_LEASE_FENCE_UNAVAILABLE",
        "WORKER_LEASE_FENCE_UNCONFIRMED",
        "WORKFLOW_CONTEXT_INCOMPATIBLE",
    }
)


class IncompatibleCheckpointVersion(RuntimeError):
    """The persisted checkpoint uses a schema this runtime cannot read."""


@dataclass(frozen=True)
class Checkpoint:
    run_id: str
    sequence: int
    state: Mapping[str, object]
    last_completed_step: str | None
    pending_action: Mapping[str, object] | None
    pending_approval: Mapping[str, object] | None
    context_version: str
    created_at: datetime
    organization_id: str
    trace_id: str

    def __post_init__(self) -> None:
        for field_name in ("run_id", "context_version", "organization_id", "trace_id"):
            _require_text(getattr(self, field_name), field_name)
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
            or self.sequence > _MAX_SEQUENCE
        ):
            raise ValueError("sequence must be a positive SQLite-compatible integer")
        if self.last_completed_step is not None:
            _require_text(self.last_completed_step, "last_completed_step")
        if not isinstance(self.created_at, datetime):
            raise TypeError("created_at must be a datetime")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        object.__setattr__(self, "state", _normalize_object(self.state, "state"))
        if self.pending_action is not None:
            object.__setattr__(
                self,
                "pending_action",
                _normalize_object(self.pending_action, "pending_action"),
            )
        if self.pending_approval is not None:
            object.__setattr__(
                self,
                "pending_approval",
                _normalize_object(self.pending_approval, "pending_approval"),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "state": _normalize_object(self.state, "state"),
            "last_completed_step": self.last_completed_step,
            "pending_action": (
                _normalize_object(self.pending_action, "pending_action")
                if self.pending_action is not None
                else None
            ),
            "pending_approval": (
                _normalize_object(self.pending_approval, "pending_approval")
                if self.pending_approval is not None
                else None
            ),
            "context_version": self.context_version,
            "created_at": self.created_at.isoformat(),
            "organization_id": self.organization_id,
            "trace_id": self.trace_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Checkpoint:
        schema_version = value.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or schema_version != CHECKPOINT_SCHEMA_VERSION
        ):
            raise IncompatibleCheckpointVersion(
                f"checkpoint payload schema {schema_version!r} is not supported"
            )
        try:
            created_at = datetime.fromisoformat(
                _require_text(value.get("created_at"), "created_at")
            )
            state = value.get("state")
            pending_action = value.get("pending_action")
            pending_approval = value.get("pending_approval")
            if not isinstance(state, Mapping):
                raise ValueError("state must be an object")
            if pending_action is not None and not isinstance(pending_action, Mapping):
                raise ValueError("pending_action must be an object or null")
            if pending_approval is not None and not isinstance(pending_approval, Mapping):
                raise ValueError("pending_approval must be an object or null")
            sequence = value.get("sequence")
            if isinstance(sequence, bool) or not isinstance(sequence, int):
                raise ValueError("sequence must be an integer")
            return cls(
                run_id=_require_text(value.get("run_id"), "run_id"),
                sequence=sequence,
                state=state,
                last_completed_step=_optional_text(
                    value.get("last_completed_step"), "last_completed_step"
                ),
                pending_action=pending_action,
                pending_approval=pending_approval,
                context_version=_require_text(value.get("context_version"), "context_version"),
                created_at=created_at,
                organization_id=_require_text(value.get("organization_id"), "organization_id"),
                trace_id=_require_text(value.get("trace_id"), "trace_id"),
            )
        except (TypeError, ValueError) as error:
            raise ValueError("persisted checkpoint is invalid") from error


class CheckpointStore:
    """Persists local workflow checkpoints and their outbox events atomically."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def save(self, checkpoint: Checkpoint) -> Checkpoint:
        if not isinstance(checkpoint, Checkpoint):
            raise TypeError("checkpoint must be a Checkpoint")
        checkpoint_json = canonical_json(checkpoint.to_dict())
        checkpoint_id = _checkpoint_id(checkpoint.run_id, checkpoint.sequence)
        with self._database.connect() as connection:
            latest_row = connection.execute(
                "SELECT checkpoint_id, payload_json, schema_version FROM checkpoints "
                "WHERE task_id = ? ORDER BY checkpoint_id DESC LIMIT 1",
                (checkpoint.run_id,),
            ).fetchone()
            if latest_row is not None:
                _check_schema_version(latest_row["schema_version"])
                latest = _decode_checkpoint(latest_row["payload_json"])
                if checkpoint.sequence < latest.sequence:
                    raise ValueError("checkpoint sequence cannot move backwards")
                if checkpoint.sequence == latest.sequence:
                    if (
                        latest_row["checkpoint_id"] == checkpoint_id
                        and latest_row["payload_json"] == checkpoint_json
                    ):
                        return latest
                    raise ValueError("checkpoint sequence is already bound to another state")

            connection.execute(
                "INSERT INTO checkpoints "
                "(checkpoint_id, task_id, payload_json, schema_version, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    checkpoint_id,
                    checkpoint.run_id,
                    checkpoint_json,
                    CHECKPOINT_SCHEMA_VERSION,
                    checkpoint.created_at.isoformat(),
                ),
            )
            event = _checkpoint_event(checkpoint)
            _insert_outbox_event(connection, event, _checkpoint_idempotency_key(checkpoint))
        return checkpoint

    def latest(self, run_id: str) -> Checkpoint | None:
        _require_text(run_id, "run_id")
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT payload_json, schema_version FROM checkpoints WHERE task_id = ? "
                "ORDER BY checkpoint_id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        _check_schema_version(row["schema_version"])
        checkpoint = _decode_checkpoint(row["payload_json"])
        if checkpoint.run_id != run_id:
            raise ValueError("persisted checkpoint run_id does not match its storage key")
        return checkpoint

    def record_decision(
        self, checkpoint: Checkpoint, *, status: str, error_code: str | None
    ) -> None:
        """Queue metadata-only recovery evidence; checkpoint contents stay private."""
        _require_text(status, "status")
        if error_code is not None:
            _require_text(error_code, "error_code")
        now = datetime.now(UTC).isoformat()
        event_id = str(uuid4())
        event = EventRecord(
            event_id=event_id,
            schema_version=1,
            timestamp=now,
            organization_id=checkpoint.organization_id,
            trace_id=checkpoint.trace_id,
            event_type="WORKFLOW_RECOVERY_DECISION",
            payload={
                "run_id": checkpoint.run_id,
                "sequence": checkpoint.sequence,
                "status": status,
                "error_code": error_code,
            },
        )
        with self._database.connect() as connection:
            _insert_outbox_event(
                connection,
                event,
                idempotency_key=f"recovery-decision:{event_id}",
            )

    def record_process_crash(
        self,
        checkpoint: Checkpoint,
        *,
        previous_generation: int,
        current_generation: int,
        status: object,
        error_code: str | None,
    ) -> None:
        """Persist metadata-only process recovery evidence idempotently."""
        if (
            isinstance(previous_generation, bool)
            or not isinstance(previous_generation, int)
            or previous_generation < 1
            or isinstance(current_generation, bool)
            or not isinstance(current_generation, int)
            or current_generation <= previous_generation
        ):
            raise ValueError("runtime generations are invalid")
        status_value = getattr(status, "value", status)
        if not isinstance(status_value, str) or status_value not in _PROCESS_RECOVERY_STATUSES:
            raise ValueError("status is not a process recovery status")
        for field_name in ("run_id", "organization_id", "trace_id"):
            field_value = getattr(checkpoint, field_name, None)
            if not isinstance(field_value, str) or _SAFE_RECOVERY_ID.fullmatch(field_value) is None:
                raise ValueError(f"{field_name} is invalid for recovery audit")
        if error_code is not None and (
            not isinstance(error_code, str)
            or error_code not in _PROCESS_RECOVERY_ERROR_CODES
        ):
            raise ValueError("error_code is invalid")
        event_key = (
            f"process-crash-recovery:{checkpoint.run_id}:"
            f"{previous_generation}:{current_generation}:"
            f"{status_value}:{error_code or 'NONE'}"
        )
        event_id = str(uuid5(NAMESPACE_URL, f"shuliu:{event_key}"))
        event_payload = {
            "run_id": checkpoint.run_id,
            "previous_generation": previous_generation,
            "current_generation": current_generation,
            "status": status_value,
            "error_code": error_code,
        }
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_json FROM outbox WHERE event_id = ? OR idempotency_key = ?",
                (event_id, event_key),
            ).fetchone()
            if existing is not None:
                try:
                    stored = json.loads(existing["payload_json"])
                except (TypeError, json.JSONDecodeError) as error:
                    raise ValueError("stored process crash event is invalid") from error
                expected = {
                    "event_id": event_id,
                    "schema_version": 1,
                    "organization_id": checkpoint.organization_id,
                    "trace_id": checkpoint.trace_id,
                    "event_type": "WORKFLOW_PROCESS_CRASH_RECOVERY",
                    "payload": event_payload,
                }
                if not isinstance(stored, dict) or any(
                    stored.get(name) != value for name, value in expected.items()
                ):
                    raise ValueError("process crash event identity conflicts with stored payload")
                return
            event = EventRecord(
                event_id=event_id,
                schema_version=1,
                timestamp=datetime.now(UTC).isoformat(),
                organization_id=checkpoint.organization_id,
                trace_id=checkpoint.trace_id,
                event_type="WORKFLOW_PROCESS_CRASH_RECOVERY",
                payload=event_payload,
            )
            _insert_outbox_event(connection, event, event_key)

def _checkpoint_event(checkpoint: Checkpoint) -> EventRecord:
    event_id = str(
        uuid5(
            NAMESPACE_URL,
            f"shuliu:checkpoint-saved:{checkpoint.run_id}:{checkpoint.sequence}",
        )
    )
    return EventRecord(
        event_id=event_id,
        schema_version=1,
        timestamp=checkpoint.created_at.isoformat(),
        organization_id=checkpoint.organization_id,
        trace_id=checkpoint.trace_id,
        event_type="CHECKPOINT_SAVED",
        payload={
            "run_id": checkpoint.run_id,
            "sequence": checkpoint.sequence,
            "context_version": checkpoint.context_version,
        },
    )


def _insert_outbox_event(
    connection: sqlite3.Connection,
    event: EventRecord,
    idempotency_key: str,
) -> None:
    payload_json = canonical_json(event_to_dict(event))
    existing = connection.execute(
        "SELECT event_id, idempotency_key, payload_json FROM outbox "
        "WHERE event_id = ? OR idempotency_key = ?",
        (event.event_id, idempotency_key),
    ).fetchone()
    if existing is not None:
        if (
            existing["event_id"] != event.event_id
            or existing["idempotency_key"] != idempotency_key
            or existing["payload_json"] != payload_json
        ):
            raise ValueError("outbox event identity conflicts with existing payload")
        return
    connection.execute(
        "INSERT INTO outbox (event_id, idempotency_key, payload_json, created_at) "
        "VALUES (?, ?, ?, ?)",
        (event.event_id, idempotency_key, payload_json, event.timestamp),
    )


def _decode_checkpoint(payload_json: str) -> Checkpoint:
    try:
        payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("persisted checkpoint JSON is invalid") from error
    if not isinstance(payload, Mapping):
        raise ValueError("persisted checkpoint must be an object")
    return Checkpoint.from_dict(payload)


def _check_schema_version(schema_version: object) -> None:
    if (
        isinstance(schema_version, bool)
        or schema_version != CHECKPOINT_SCHEMA_VERSION
    ):
        raise IncompatibleCheckpointVersion(
            f"checkpoint schema {schema_version!r} is not supported"
        )


def _checkpoint_id(run_id: str, sequence: int) -> str:
    return f"{run_id}:checkpoint:{sequence:020d}"


def _checkpoint_idempotency_key(checkpoint: Checkpoint) -> str:
    return f"checkpoint-saved:{checkpoint.run_id}:{checkpoint.sequence}"


def _normalize_object(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be an object")
    try:
        normalized = json.loads(
            json.dumps(dict(value), ensure_ascii=False, allow_nan=False)
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must contain JSON values") from error
    if not isinstance(normalized, dict):
        raise TypeError(f"{field_name} must be an object")
    return normalized


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")
    return value


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name)
