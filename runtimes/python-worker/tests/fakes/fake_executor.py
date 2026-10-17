from __future__ import annotations

from collections.abc import Mapping


class FakeExecutor:
    def __init__(
        self,
        *,
        output: object | None = None,
        verified: bool = True,
        order: list[str] | None = None,
    ) -> None:
        self.output = output if output is not None else {"ok": True}
        self.verified = verified
        self.order = order if order is not None else []
        self.calls: list[tuple[str, dict[str, object], dict[str, object]]] = []

    def execute(
        self,
        tool_name: str,
        args: Mapping[str, object],
        context: Mapping[str, object],
        idempotency_key: str | None,
    ) -> object:
        self.order.append("executor")
        self.calls.append((tool_name, dict(args), dict(context), idempotency_key))
        return self.output

    def verify(
        self,
        tool_name: str,
        args: Mapping[str, object],
        output: object,
        context: Mapping[str, object],
    ) -> bool:
        self.order.append("verify")
        return self.verified
