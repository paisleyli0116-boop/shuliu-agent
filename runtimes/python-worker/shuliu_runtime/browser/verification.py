from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit


class BrowserError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def origin(url: str) -> str:
    try:
        parts = urlsplit(url)
        if (parts.scheme not in ("http", "https") or not parts.hostname
                or parts.username is not None or parts.password is not None
                or any(ord(char) < 33 for char in url) or "\\" in url):
            raise ValueError()
        host = parts.hostname.encode("idna").decode("ascii").lower()
        if ":" in host:
            host = "[" + host + "]"
        port = parts.port or (443 if parts.scheme == "https" else 80)
        return f"{parts.scheme}://{host}:{port}"
    except (ValueError, UnicodeError):
        raise BrowserError("BROWSER_TARGET_DENIED") from None


def check_url(url: str, allowed: frozenset[str]) -> None:
    if url != "about:blank" and origin(url) not in allowed:
        raise BrowserError("BROWSER_TARGET_DENIED")


def safe_text(value: object, limit: int = 8000) -> str:
    text = str(value)[:limit]
    text = re.sub(r"(?i)\b(password|secret|api[_-]?key|token|authorization|cookie)\s*[:=]\s*[^\s;,]+", r"\1=[REDACTED]", text)
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]", text)
    return text[:limit]


def safe_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, safe_text(parts.path, 512), "", ""))


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def verified(operation: str, args: dict, before: dict, after: dict) -> bool:
    if operation == "navigate":
        return after["url"] == args["url"]
    if before.get("account") != after.get("account"):
        return False
    if operation == "scroll":
        delta = after.get("scroll_y", 0) - before.get("scroll_y", 0)
        return delta * args["delta_y"] > 0
    expected = args.get("expected_text")
    # A pre-existing marker or merely receiving a click event proves nothing.
    return bool(expected and expected not in before.get("text", "") and expected in after.get("text", ""))
