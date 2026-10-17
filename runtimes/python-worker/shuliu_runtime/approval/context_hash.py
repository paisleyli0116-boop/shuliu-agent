from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping


def action_context_digest(action_context: Mapping[str, object]) -> str:
    """Hash a complete action context using a stable, JSON-only encoding."""
    if not isinstance(action_context, Mapping):
        raise TypeError("action_context must be a mapping")
    try:
        canonical = json.dumps(
            action_context,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError(
            "action_context must contain only finite JSON values"
        ) from error
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
