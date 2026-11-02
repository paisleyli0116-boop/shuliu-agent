from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from shuliu_runtime.persistence.sqlite import SQLiteDatabase
from shuliu_runtime.events.models import EventRecord, event_to_dict

from .safe_mode import SafeModeReason

_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class PersistedSafeMode:
    active: bool
    error_code: str | None = None
    run_ids: tuple[str, ...] = ()


class SQLiteSafeModeController:
    """Persists a bounded safe-mode marker without checkpoint or exception data."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def enter(self, *, run_id: str, reason: SafeModeReason) -> None:
        if not isinstance(run_id, str) or _SAFE_RUN_ID.fullmatch(run_id) is None or not isinstance(reason, SafeModeReason):
            raise ValueError("safe-mode marker is invalid")
        state = json.dumps({"status": "SAFE_MODE", "reason": reason.value}, sort_keys=True)
        with self._database.connect() as connection:
            connection.execute(
                "INSERT INTO local_task_state (task_id, state, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(task_id) DO UPDATE SET state = excluded.state, "
                "updated_at = excluded.updated_at",
                (f"safe-mode:{run_id}", state, datetime.now(UTC).isoformat()),
            )

    def status(self) -> PersistedSafeMode:
        """Return a fail-closed runtime status from durable safe-mode markers."""
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT task_id, state FROM local_task_state WHERE task_id LIKE 'safe-mode:%'"
            ).fetchall()
        if not rows:
            return PersistedSafeMode(active=False)

        allowed_reasons = {reason.value for reason in SafeModeReason}
        run_ids: list[str] = []
        for row in rows:
            task_id = row["task_id"]
            run_id = task_id.removeprefix("safe-mode:") if isinstance(task_id, str) else ""
            try:
                value = json.loads(row["state"])
            except (TypeError, json.JSONDecodeError):
                return PersistedSafeMode(active=True, error_code="RUNTIME_SAFE_MODE_STATE_INVALID")
            if (
                _SAFE_RUN_ID.fullmatch(run_id) is None
                or
                not isinstance(value, dict)
                or value.get("status") != "SAFE_MODE"
                or value.get("reason") not in allowed_reasons
            ):
                return PersistedSafeMode(active=True, error_code="RUNTIME_SAFE_MODE_STATE_INVALID")
            run_ids.append(run_id)
        return PersistedSafeMode(active=True, error_code="RUNTIME_SAFE_MODE", run_ids=tuple(sorted(run_ids)))

    def release_after_verified_recovery(
        self,
        *,
        recovered: dict[str, tuple[str, str]],
        transition_id: str,
        current_generation: int,
        cancelled: threading.Event | None = None,
    ) -> bool:
        """Clear markers only for runs durably recovered as RESUME in this generation.

        The caller owns the recovery protocol and supplies only checkpoint identities
        captured after the current transition. Marker deletion and the audit outbox
        write share one transaction so a crash cannot silently clear Safe Mode. When
        provided, cancellation is checked after acquiring the write transaction; once
        that admission check passes, the atomic release commit wins over late cancel.
        """
        if (
            not isinstance(recovered, dict)
            or not isinstance(transition_id, str)
            or not transition_id
            or isinstance(current_generation, bool)
            or not isinstance(current_generation, int)
            or current_generation < 1
        ):
            return False
        try:
            with self._database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if cancelled is not None and cancelled.is_set():
                    return False
                rows = connection.execute(
                    "SELECT task_id, state, updated_at FROM local_task_state "
                    "WHERE task_id LIKE 'safe-mode:%' ORDER BY task_id"
                ).fetchall()
                prepared: list[tuple[str, str, str, str, str]] = []
                allowed_reasons = {reason.value for reason in SafeModeReason}
                for row in rows:
                    task_id = row["task_id"]
                    run_id = task_id.removeprefix("safe-mode:") if isinstance(task_id, str) else ""
                    try:
                        value = json.loads(row["state"])
                    except (TypeError, json.JSONDecodeError):
                        return False
                    identity = recovered.get(run_id)
                    if (
                        _SAFE_RUN_ID.fullmatch(run_id) is None
                        or not isinstance(value, dict)
                        or value.get("status") != "SAFE_MODE"
                        or value.get("reason") not in allowed_reasons
                        or not isinstance(identity, tuple)
                        or len(identity) != 2
                        or any(not isinstance(item, str) or not item.strip() for item in identity)
                    ):
                        return False
                    organization_id, trace_id = identity
                    marker_time = row["updated_at"]
                    event_key = f"safe-mode-release:{run_id}:{transition_id}"
                    event_id = str(uuid5(NAMESPACE_URL, f"shuliu:{event_key}"))
                    event = EventRecord(
                        event_id=event_id,
                        schema_version=1,
                        timestamp=marker_time,
                        organization_id=organization_id,
                        trace_id=trace_id,
                        event_type="RUNTIME_SAFE_MODE_RELEASED",
                        payload={
                            "run_id": run_id,
                            "current_generation": current_generation,
                            "transition_id": transition_id,
                            "decision": "RESUME",
                        },
                    )
                    payload_json = json.dumps(event_to_dict(event), sort_keys=True, separators=(",", ":"))
                    existing = connection.execute(
                        "SELECT event_id, payload_json FROM outbox WHERE idempotency_key = ?",
                        (event_key,),
                    ).fetchone()
                    if existing is not None and (
                        existing["event_id"] != event_id or existing["payload_json"] != payload_json
                    ):
                        return False
                    prepared.append((run_id, marker_time, event_key, event_id, payload_json))

                for run_id, marker_time, event_key, event_id, payload_json in prepared:
                    connection.execute(
                        "INSERT OR IGNORE INTO outbox "
                        "(event_id, idempotency_key, payload_json, created_at) VALUES (?, ?, ?, ?)",
                        (event_id, event_key, payload_json, marker_time),
                    )
                    cursor = connection.execute(
                        "DELETE FROM local_task_state WHERE task_id = ?",
                        (f"safe-mode:{run_id}",),
                    )
                    if cursor.rowcount != 1:
                        raise _SafeModeReleaseConflict
        except _SafeModeReleaseConflict:
            return False
        return True


class _SafeModeReleaseConflict(RuntimeError):
    pass
