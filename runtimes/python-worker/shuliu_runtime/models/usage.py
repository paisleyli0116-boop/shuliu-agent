from __future__ import annotations

from collections.abc import Mapping


SAFE_USAGE_FIELDS = frozenset({"prompt_tokens", "completion_tokens", "total_tokens"})


def validate_usage(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise ValueError("model call usage is invalid")
    result: dict[str, int] = {}
    for key, amount in value.items():
        if (
            key not in SAFE_USAGE_FIELDS
            or isinstance(amount, bool)
            or not isinstance(amount, int)
            or amount < 0
        ):
            raise ValueError("model call usage is invalid")
        result[key] = amount
    return result
