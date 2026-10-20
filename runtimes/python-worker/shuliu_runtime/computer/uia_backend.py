from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from .verification import ComputerError


_SENSITIVE_NAME = re.compile(r"(?i)(password|passcode|secret|token|api\s*key|credential)")


class UiaBackend:
    """UI Automation client. It accepts only observed element identities."""

    def __init__(self) -> None:
        self._targets: dict[tuple[str, str], object] = {}

    def observe(self, window_ref: str, window_info: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(window_info.get("hwnd"), int):
            raise ComputerError("COMPUTER_WINDOW_UNAVAILABLE")
        try:
            from pywinauto import Desktop

            window = Desktop(backend="uia").window(handle=window_info["hwnd"]).wrapper_object()
            raw_elements: list[dict[str, object]] = []
            self._targets = {
                key: target for key, target in self._targets.items() if key[0] != window_ref
            }
            for control in window.descendants()[:300]:
                info = control.element_info
                control_type = _text(getattr(info, "control_type", "Unknown"), 64)
                name = _text(getattr(info, "name", ""), 256)
                automation_id = _text(getattr(info, "automation_id", ""), 128)
                class_name = _text(getattr(info, "class_name", ""), 128)
                runtime_id = _runtime_id(getattr(info, "runtime_id", None))
                rect = info.rectangle
                bounds = [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]
                sensitive = bool(getattr(info, "is_password", False)) or bool(
                    _SENSITIVE_NAME.search(name)
                )
                element_id = hashlib.sha256(
                    json.dumps(
                        [window_ref, automation_id, class_name, control_type, runtime_id],
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()[:32]
                value: object = None
                if not sensitive and control_type == "Edit":
                    try:
                        value = control.get_value()
                    except Exception:
                        value = control.window_text()
                    value = _text(value, 8192)
                elif not sensitive:
                    value = _safe_control_value(control)
                scroll_percent = _current_scroll_percent(control)
                raw_elements.append(
                    {
                        "element_id": element_id,
                        "runtime_id": runtime_id,
                        "control_type": control_type,
                        "name": "[REDACTED]" if sensitive else name,
                        "value": value,
                        "sensitive": sensitive,
                        "enabled": bool(info.enabled),
                        "focused": bool(getattr(info, "has_keyboard_focus", False)),
                        "bounds": bounds,
                        "scroll_percent": scroll_percent,
                    }
                )
                self._targets[(window_ref, element_id)] = control
            return {
                "window_ref": window_ref,
                "title": window_info.get("title", ""),
                "process_id": window_info.get("process_id"),
                "bounds": list(window_info["bounds"]),
                "dpi": window_info.get("dpi"),
                "elements": raw_elements,
                "scroll_y": None,
            }
        except ComputerError:
            raise
        except Exception:
            raise ComputerError("COMPUTER_UIA_UNAVAILABLE") from None

    def perform(
        self,
        window_ref: str,
        action: Mapping[str, object],
        element_id: str,
    ) -> None:
        target = self._targets.get((window_ref, element_id))
        if target is None:
            raise ComputerError("COMPUTER_ELEMENT_STALE")
        try:
            if action.get("kind") == "click":
                target.click_input()
            elif action.get("kind") == "type":
                info = target.element_info
                if bool(getattr(info, "is_password", False)) or _SENSITIVE_NAME.search(
                    _text(getattr(info, "name", ""), 256)
                ):
                    raise ComputerError("COMPUTER_SENSITIVE_TARGET_DENIED")
                text = action.get("text")
                if not isinstance(text, str):
                    raise ComputerError("COMPUTER_ACTION_INVALID")
                target.set_edit_text(text)
            elif action.get("kind") == "scroll":
                delta = action.get("delta_y")
                if not isinstance(delta, int) or isinstance(delta, bool) or delta == 0:
                    raise ComputerError("COMPUTER_ACTION_INVALID")
                wheel_dist = -max(-10, min(10, int(round(delta / 120))))
                target.wheel_mouse_input(wheel_dist=wheel_dist)
            else:
                raise ComputerError("COMPUTER_ACTION_UNSUPPORTED")
        except ComputerError:
            raise
        except Exception:
            raise ComputerError("COMPUTER_ACTION_DISPATCH_FAILED") from None


def _safe_control_value(control: object) -> object:
    try:
        value = control.get_toggle_state()
        if isinstance(value, (bool, int)):
            return value
    except Exception:
        pass
    try:
        return bool(control.is_checked())
    except Exception:
        return None


def _current_scroll_percent(control: object) -> int | None:
    try:
        value = control.iface_scroll.CurrentVerticalScrollPercent
        if isinstance(value, (int, float)) and 0 <= value <= 100:
            return int(round(value))
    except Exception:
        pass
    return None


def _runtime_id(value: object) -> list[int]:
    if (
        not isinstance(value, (tuple, list))
        or not value
        or len(value) > 64
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
    ):
        raise ComputerError("COMPUTER_ELEMENT_IDENTITY_UNAVAILABLE")
    return list(value)


def _text(value: object, limit: int) -> str:
    text = str(value)[:limit]
    text = re.sub(
        r"(?i)\b(password|secret|api[_ -]?key|token|authorization|cookie)\s*[:=]\s*[^\s;,]+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", text)
    return text[:limit]
