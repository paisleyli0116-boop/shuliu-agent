from __future__ import annotations

from datetime import UTC, datetime
from threading import Event
from typing import Any

from .credentials import WorkerAuthenticationError
from .protocol import WorkerMessage


class WorkerHost:
    """Run an authenticated Worker connection without inventing task ownership.

    The host polls only Cloud assignments through WorkerTaskRunner.run_assigned.
    Connection failures propagate to the service manager; this class never retries
    or replays a task whose lease or ACK outcome is unknown.
    """

    def __init__(
        self,
        *,
        connection: Any,
        runner: Any,
        idle_poll_seconds: float = 1.0,
        clock=None,
    ) -> None:
        if not callable(getattr(connection, "__enter__", None)) or not callable(
            getattr(connection, "__exit__", None)
        ):
            raise TypeError("authenticated Worker connection context is required")
        if not callable(getattr(connection, "send", None)):
            raise TypeError("authenticated Worker connection is required")
        if not callable(getattr(runner, "run_assigned", None)):
            raise TypeError("assigned-task runner is required")
        if (
            isinstance(idle_poll_seconds, bool)
            or not isinstance(idle_poll_seconds, (int, float))
            or not 0.01 <= idle_poll_seconds <= 60
        ):
            raise ValueError("idle polling interval is outside the supported range")
        if clock is not None and not callable(clock):
            raise TypeError("Worker host clock is invalid")
        self._connection = connection
        self._runner = runner
        self._idle_poll_seconds = float(idle_poll_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def with_tool_broker(
        cls,
        *,
        connection: Any,
        tool_broker: Any,
        idle_poll_seconds: float = 1.0,
        clock=None,
    ) -> WorkerHost:
        """Compose the production runner that enforces Cloud lease fencing."""
        from .task_runner import WorkerTaskRunner

        runner = WorkerTaskRunner(
            connection=connection,
            tool_broker=tool_broker,
            clock=clock,
        )
        return cls(
            connection=connection,
            runner=runner,
            idle_poll_seconds=idle_poll_seconds,
            clock=clock,
        )

    def run(self, *, stop_event: Event) -> None:
        if not isinstance(stop_event, Event):
            raise TypeError("Worker host requires a threading Event stop signal")
        with self._connection:
            while not stop_event.is_set():
                worker_id = getattr(self._connection, "worker_id", None)
                if not isinstance(worker_id, str) or not worker_id.strip():
                    raise WorkerAuthenticationError(
                        "WORKER_IDENTITY_MISMATCH",
                        "authenticated Worker identity is unavailable",
                    )
                now = self._clock()
                if now.tzinfo is None or now.utcoffset() is None:
                    raise WorkerAuthenticationError(
                        "WORKER_CLOCK_INVALID", "Worker host clock is invalid"
                    )
                heartbeat = self._connection.send(
                    "heartbeat",
                    {
                        "worker_id": worker_id,
                        "sent_at": now.astimezone(UTC).isoformat(),
                    },
                )
                if (
                    not isinstance(heartbeat, WorkerMessage)
                    or heartbeat.message_type != "heartbeat"
                    or heartbeat.payload.get("worker_id") != worker_id
                ):
                    raise WorkerAuthenticationError(
                        "WORKER_HEARTBEAT_RESPONSE_INVALID",
                        "Cloud returned an invalid heartbeat response",
                    )
                result = self._runner.run_assigned()
                if result.message_type == "no_assignment":
                    if result.payload.get("worker_id") != worker_id:
                        raise WorkerAuthenticationError(
                            "WORKER_IDENTITY_MISMATCH",
                            "Cloud assignment response belongs to a different Worker",
                        )
                    stop_event.wait(self._idle_poll_seconds)
                elif result.message_type != "ack":
                    raise WorkerAuthenticationError(
                        "WORKER_TASK_RESULT_INVALID",
                        "Worker task runner returned no acknowledged result",
                    )
