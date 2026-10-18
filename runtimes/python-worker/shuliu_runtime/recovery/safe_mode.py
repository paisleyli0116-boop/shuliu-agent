from __future__ import annotations

from enum import Enum
from typing import Protocol


class SafeModeReason(str, Enum):
    CHECKPOINT_INCOMPATIBLE = "CHECKPOINT_INCOMPATIBLE"
    CHECKPOINT_UNAVAILABLE = "CHECKPOINT_UNAVAILABLE"
    CONTEXT_INCOMPATIBLE = "CONTEXT_INCOMPATIBLE"


class SafeModeController(Protocol):
    def enter(self, *, run_id: str, reason: SafeModeReason) -> None: ...
