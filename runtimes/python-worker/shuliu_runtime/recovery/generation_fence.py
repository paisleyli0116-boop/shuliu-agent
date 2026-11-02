from __future__ import annotations

import re

from shuliu_runtime.persistence.sqlite import SQLiteDatabase


_TRANSITION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class SQLiteRuntimeGenerationFence:
    """Atomically advances the Sidecar generation before checkpoint recovery."""

    def __init__(self, database: SQLiteDatabase, runtime_id: str = "sidecar") -> None:
        if not runtime_id or len(runtime_id) > 128:
            raise ValueError("runtime_id is invalid")
        self._database = database
        self._runtime_id = runtime_id

    def begin_transition(
        self,
        *,
        transition_id: str,
        has_prior_checkpoints: bool,
    ) -> tuple[int, int] | None:
        if (
            not isinstance(transition_id, str)
            or _TRANSITION_ID.fullmatch(transition_id) is None
            or not isinstance(has_prior_checkpoints, bool)
        ):
            return None
        with self._database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT generation, transition_id FROM runtime_generation WHERE runtime_id = ?",
                (self._runtime_id,),
            ).fetchone()
            if row is None:
                if has_prior_checkpoints:
                    return None
                connection.execute(
                    "INSERT INTO runtime_generation (runtime_id, generation, transition_id) "
                    "VALUES (?, 1, ?)",
                    (self._runtime_id, transition_id),
                )
                return (0, 1)
            current = row["generation"]
            if row["transition_id"] == transition_id:
                # Retries for a single launch are idempotent.
                return (current - 1, current)
            if isinstance(current, bool) or not isinstance(current, int) or current < 1:
                return None
            next_generation = current + 1
            cursor = connection.execute(
                "UPDATE runtime_generation SET generation = ?, transition_id = ? "
                "WHERE runtime_id = ? AND generation = ?",
                (next_generation, transition_id, self._runtime_id, current),
            )
            return (current, next_generation) if cursor.rowcount == 1 else None

    def confirm_transition(
        self,
        *,
        previous_generation: int,
        current_generation: int,
        transition_id: str,
    ) -> bool:
        if (
            isinstance(previous_generation, bool)
            or not isinstance(previous_generation, int)
            or previous_generation < 1
            or isinstance(current_generation, bool)
            or not isinstance(current_generation, int)
            or current_generation != previous_generation + 1
            or not isinstance(transition_id, str)
            or _TRANSITION_ID.fullmatch(transition_id) is None
        ):
            return False
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT generation, transition_id FROM runtime_generation WHERE runtime_id = ?",
                (self._runtime_id,),
            ).fetchone()
        return (
            row is not None
            and row["generation"] == current_generation
            and row["transition_id"] == transition_id
        )

    def is_current_transition(self, *, current_generation: int, transition_id: str) -> bool:
        if (
            isinstance(current_generation, bool)
            or not isinstance(current_generation, int)
            or current_generation < 1
            or not isinstance(transition_id, str)
            or _TRANSITION_ID.fullmatch(transition_id) is None
        ):
            return False
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT generation, transition_id FROM runtime_generation WHERE runtime_id = ?",
                (self._runtime_id,),
            ).fetchone()
        return row is not None and row["generation"] == current_generation and row["transition_id"] == transition_id

    def fence_previous(
        self,
        run_id: str,
        *,
        previous_generation: int,
        current_generation: int,
        transition_id: str,
    ) -> bool:
        if not isinstance(run_id, str) or not run_id.strip():
            return False
        return self.confirm_transition(
            previous_generation=previous_generation,
            current_generation=current_generation,
            transition_id=transition_id,
        )
