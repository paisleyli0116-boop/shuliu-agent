from __future__ import annotations

from collections.abc import Mapping


class FakeToolExecutor:
    """Controllable Tool executor for end-to-end fixtures.

    The executor never touches real systems. It records every dispatch so
    tests can assert that a retried step did not duplicate side effects, and
    it can be told to fail a bounded number of times to simulate a lost ack
    or a crashed dispatch.
    """

    def __init__(
        self,
        *,
        output: object | None = None,
        verified: bool = True,
    ) -> None:
        self.output = (
            output
            if output is not None
            else {"ok": True, "evidence_ref": "e2e-evidence-1"}
        )
        self.verified = verified
        self.failures_remaining = 0
        self.calls: list[dict[str, object]] = []
        self.side_effects: list[dict[str, object]] = []

    def fail_next(self, times: int) -> None:
        if times < 0:
            raise ValueError("failure count must not be negative")
        self.failures_remaining = times

    def execute(
        self,
        tool_name: str,
        args: Mapping[str, object],
        context: Mapping[str, object],
        idempotency_key: str | None,
    ) -> object:
        self.calls.append(
            {
                "tool_id": tool_name,
                "args": dict(args),
                "idempotency_key": idempotency_key,
            }
        )
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            # No durable side effect was produced; the dispatch result is
            # unknown from the caller's perspective, mirroring a lost ack.
            raise RuntimeError("simulated dispatch crash before any side effect")
        self.side_effects.append(dict(args))
        return self.output

    def verify(
        self,
        tool_name: str,
        args: Mapping[str, object],
        output: object,
        context: Mapping[str, object],
    ) -> bool:
        return self.verified
