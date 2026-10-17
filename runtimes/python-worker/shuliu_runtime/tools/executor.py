from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class ToolExecutor(Protocol):
    """Performs one registered operation and verifies its resulting state."""

    def execute(
        self,
        tool_name: str,
        args: Mapping[str, object],
        context: Mapping[str, object],
        idempotency_key: str | None,
    ) -> object: ...

    def verify(
        self,
        tool_name: str,
        args: Mapping[str, object],
        output: object,
        context: Mapping[str, object],
    ) -> bool: ...
