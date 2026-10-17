from __future__ import annotations

from enum import Enum


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


def parse_risk_level(value: object) -> RiskLevel | None:
    if isinstance(value, RiskLevel):
        return value
    if not isinstance(value, str):
        return None
    try:
        return RiskLevel(value)
    except ValueError:
        return None
