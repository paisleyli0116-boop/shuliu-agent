from __future__ import annotations

from collections.abc import Mapping
from math import isfinite


class UfoOperatorError(ValueError):
    def __init__(self, code: str = "COMPUTER_OPERATOR_PREDICTION_INVALID") -> None:
        super().__init__(code)
        self.code = code


class UfoOperator:
    """Translate UI-TARS/UFO proposals into observed semantic UI targets.

    This class cannot access a desktop. Its output remains an untrusted proposal
    and must be submitted through ComputerRuntime and ToolBroker.
    """

    def to_action(
        self,
        prediction: Mapping[str, object],
        observation: Mapping[str, object],
    ) -> dict[str, object]:
        if (
            not isinstance(prediction, Mapping)
            or not isinstance(observation, Mapping)
            or observation.get("trust") != "UNTRUSTED_UI_CONTENT"
            or not _non_empty(observation.get("observation_id"))
            or not _non_empty(observation.get("window_ref"))
        ):
            raise UfoOperatorError()
        operation = prediction.get("operation", prediction.get("action"))
        if not isinstance(operation, str):
            raise UfoOperatorError()
        operation = operation.casefold()
        if operation in ("click", "click_input", "left_single"):
            kind = "click"
        elif operation in ("type", "type_text"):
            kind = "type"
        elif operation in ("scroll", "wheel_mouse_input"):
            kind = "scroll"
        else:
            raise UfoOperatorError("COMPUTER_OPERATOR_ACTION_UNSUPPORTED")

        elements = observation.get("elements")
        if not isinstance(elements, list):
            raise UfoOperatorError()
        target = self._target(prediction, elements, kind)
        action: dict[str, object] = {
            "kind": kind,
            "window_ref": observation["window_ref"],
            "observation_id": observation["observation_id"],
            "element_ref": target["element_ref"],
        }
        if kind == "type":
            text = prediction.get("text", prediction.get("content"))
            if not isinstance(text, str) or len(text) > 4096 or target.get("sensitive") is True:
                raise UfoOperatorError()
            action["text"] = text
        elif kind == "scroll":
            direction = prediction.get("direction")
            if direction not in ("up", "down"):
                raise UfoOperatorError()
            action["delta_y"] = -120 if direction == "up" else 120
        elif isinstance(prediction.get("expected_text"), str):
            action["expected_text"] = prediction["expected_text"]
        return action

    @staticmethod
    def _target(
        prediction: Mapping[str, object], elements: list[object], kind: str
    ) -> Mapping[str, object]:
        reference = prediction.get("element_ref")
        if isinstance(reference, str):
            found = next(
                (
                    item
                    for item in elements
                    if isinstance(item, Mapping) and item.get("element_ref") == reference
                ),
                None,
            )
            if found is None:
                raise UfoOperatorError("COMPUTER_OPERATOR_TARGET_UNOBSERVED")
            return UfoOperator._check_target(found, kind)

        x, y = prediction.get("x"), prediction.get("y")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) for value in (x, y)):
            raise UfoOperatorError()
        candidates = []
        for item in elements:
            if not isinstance(item, Mapping):
                continue
            bounds = item.get("bounds")
            if (
                not isinstance(bounds, list)
                or len(bounds) != 4
                or any(isinstance(edge, bool) or not isinstance(edge, int) for edge in bounds)
            ):
                continue
            left, top, right, bottom = bounds
            if left <= x < right and top <= y < bottom:
                candidates.append(item)
        if not candidates:
            raise UfoOperatorError("COMPUTER_OPERATOR_TARGET_UNOBSERVED")
        candidate = min(
            candidates,
            key=lambda item: (item["bounds"][2] - item["bounds"][0])
            * (item["bounds"][3] - item["bounds"][1]),
        )
        return UfoOperator._check_target(candidate, kind)

    @staticmethod
    def _check_target(item: Mapping[str, object], kind: str) -> Mapping[str, object]:
        if (
            not _non_empty(item.get("element_ref"))
            or item.get("sensitive") is True
            or item.get("enabled") is False
            or (kind == "type" and item.get("role") != "Edit")
            or (kind == "scroll" and item.get("scrollable") is not True)
        ):
            raise UfoOperatorError("COMPUTER_OPERATOR_TARGET_DENIED")
        return item


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
