from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import Enum


class ComputerError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ComputerActionStatus(str, Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


def snapshot_fingerprint(snapshot: Mapping[str, object]) -> str:
    """Hash the observed window state so actions cannot use a stale UI tree."""
    try:
        encoded = json.dumps(
            snapshot,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise ComputerError("COMPUTER_OBSERVATION_INVALID") from None
    return hashlib.sha256(encoded).hexdigest()


def verify_action(
    action: Mapping[str, object],
    before: Mapping[str, object],
    after: Mapping[str, object],
    element_id: str,
) -> bool:
    """Require a visible state change; dispatch alone never proves success."""
    before_elements = _elements_by_id(before)
    after_elements = _elements_by_id(after)
    old = before_elements.get(element_id)
    new = after_elements.get(element_id)
    if old is None or new is None:
        return False

    kind = action.get("kind")
    if kind == "type":
        return (
            old.get("sensitive") is not True
            and new.get("sensitive") is not True
            and isinstance(action.get("text"), str)
            and new.get("value") == action.get("text")
        )
    if kind == "click":
        old_value, new_value = old.get("value"), new.get("value")
        if isinstance(old_value, (bool, str, int)) and isinstance(
            new_value, (bool, str, int)
        ) and old_value != new_value:
            return True
        expected_text = action.get("expected_text")
        if not isinstance(expected_text, str) or not expected_text:
            return False
        return expected_text not in _snapshot_text(before) and expected_text in _snapshot_text(after)
    if kind == "scroll":
        old_offset = old.get("scroll_percent")
        new_offset = new.get("scroll_percent")
        delta = action.get("delta_y")
        return (
            old_offset is not None
            and new_offset is not None
            and isinstance(delta, int)
            and not isinstance(delta, bool)
            and (new_offset - old_offset) * delta > 0
        )
    return False


def _elements_by_id(snapshot: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    raw = snapshot.get("elements")
    if not isinstance(raw, list):
        return {}
    return {
        item["element_id"]: item
        for item in raw
        if isinstance(item, Mapping) and isinstance(item.get("element_id"), str)
    }


def _snapshot_text(snapshot: Mapping[str, object]) -> str:
    values: list[str] = []
    elements = snapshot.get("elements")
    if isinstance(elements, list):
        for element in elements:
            if isinstance(element, Mapping) and element.get("sensitive") is not True:
                name = element.get("name")
                value = element.get("value")
                if isinstance(name, str):
                    values.append(name)
                if isinstance(value, str):
                    values.append(value)
    title = snapshot.get("title")
    if isinstance(title, str):
        values.append(title)
    return "\n".join(values)
