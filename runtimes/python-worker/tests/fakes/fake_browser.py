"""Deterministic driver double; never real-browser integration evidence."""
from copy import deepcopy
from urllib.parse import urlsplit


class FakeBrowser:
    def __init__(self):
        self.calls, self.profiles = [], []
        self.closed, self.fail_operation, self.change_on_click = False, None, True
        self.page = {
            "url": "about:blank", "account": "fixture-user", "text": "Fixture ready", "scroll_y": 0,
            "elements": [
                {"node_id": "1", "tag": "BUTTON", "type": "button", "label": "Increment", "sensitive": False},
                {"node_id": "2", "tag": "INPUT", "type": "text", "label": "Name", "sensitive": False},
                {"node_id": "3", "tag": "INPUT", "type": "password", "label": "Password", "sensitive": True},
            ],
        }

    def request(self, operation, payload, *, timeout, cancel_event=None):
        self.calls.append(operation)
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError()
        if self.fail_operation == operation:
            raise ConnectionError("sensitive-driver-error-must-not-escape")
        if operation == "start":
            self.profiles.append(payload["profile"])
            return {"browser_version": "FAKE", "process_id": 0}
        if operation == "observe":
            return deepcopy(self.page)
        if operation == "navigate":
            self.page["url"] = payload["url"]
        if operation == "click" and self.change_on_click:
            self.page["text"] = "Counter: 1"
        if operation == "type":
            self.page["text"] = "Typed: " + payload["text"]
        if operation == "scroll":
            self.page["scroll_y"] += payload["delta_y"]
        return {}

    def close(self):
        self.closed = True


class LoopbackFixtureGate:
    """Test-only gate. No production side-effect authorization."""
    def __init__(self, origin="http://127.0.0.1:8765"):
        self.origin = origin

    def authorize(self, operation, url):
        parsed = urlsplit(url)
        return (parsed.hostname == "127.0.0.1" and parsed.scheme == "http"
                and f"{parsed.scheme}://{parsed.netloc}" == self.origin)
