from __future__ import annotations
import base64
import ctypes
from ctypes import wintypes
import datetime as dt
import getpass
import hashlib
import hmac
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TARGET = "ShuLiuDevAutopilot/FeishuWebhook"
ALLOWED_HOSTS = {"open.feishu.cn"}
HOOK_PATH = re.compile(r"^/open-apis/bot/v2/hook/[A-Za-z0-9-]+$")
CATEGORIES = {
    "github_auth": "GitHub 登录授权失效或尚未完成",
    "provider_input": "需要在数流本机设置页面输入 Provider API Key",
    "provider_approval": "需要你明确批准一次真实 Provider 请求",
    "security_review": "需要你完成 Provider 或数据安全审查",
    "business_decision": "缺少必须由你决定的业务规则",
    "security_failure": "严重安全测试失败，自动流程已停止",
    "repair_limit": "自动修复达到上限，问题仍未解决",
    "service_crash": "自动开发服务异常中断，未能自行恢复",
    "external_dependency": "当前任务缺少计划要求的外部环境或授权",
    "deployment_approval": "正式部署需要你明确批准",
}
_SECRET_RE = re.compile(
    r"(?i)(api[-_ ]?key|token|password|secret|credential|authorization|cookie)\s*[:=]\s*\S+"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_TOKEN_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{12,})\b")


class NotificationError(RuntimeError):
    pass


def valid_webhook(url: str) -> bool:
    try:
        part = urllib.parse.urlsplit(url)
        return (
            part.scheme == "https"
            and part.hostname in ALLOWED_HOSTS
            and part.username is None
            and part.password is None
            and not part.query
            and not part.fragment
            and HOOK_PATH.fullmatch(part.path) is not None
        )
    except (TypeError, ValueError):
        return False


def _safe_text(value, limit=300):
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    text = _SECRET_RE.sub(r"\1=[REDACTED]", text)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _TOKEN_RE.sub("[REDACTED]", text)
    text = re.sub(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9-]+", "[WEBHOOK]", text)
    return text[:limit]


def format_alert(*, category, task, attempt_summary="", user_action="", resume="", **_ignored):
    if category not in CATEGORIES:
        raise NotificationError("unsupported notification category")
    if not task or not user_action or not resume:
        raise NotificationError("incomplete blocker notification")
    return "\n".join((
        "ShuLiu Autopilot 需要你处理一个阻塞",
        f"当前任务：{_safe_text(task, 40)}",
        f"具体阻塞：{CATEGORIES[category]}",
        f"已尝试：{_safe_text(attempt_summary or '已按任务门禁检查；没有重复执行外部操作。')}",
        f"唯一需要做的事：{_safe_text(user_action)}",
        f"如何恢复：{_safe_text(resume)}",
    ))


def signed_payload(text, secret, timestamp=None):
    if not text or not secret:
        raise NotificationError("missing notification content or signature secret")
    timestamp = int(timestamp if timestamp is not None else dt.datetime.now(dt.timezone.utc).timestamp())
    signing_key = f"{timestamp}\n{secret}".encode("utf-8")
    signature = base64.b64encode(hmac.new(signing_key, b"", hashlib.sha256).digest()).decode("ascii")
    return {
        "timestamp": str(timestamp),
        "sign": signature,
        "msg_type": "text",
        "content": {"text": _safe_text(text, 3500)},
    }


def send_alert(credentials, text, *, opener=None, now=None):
    url = credentials.get("webhook_url", "")
    secret = credentials.get("signing_secret", "")
    if not valid_webhook(url) or not secret:
        return {"sent": False, "error_category": "notification_not_configured"}
    payload = signed_payload(text, secret, now)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > 18 * 1024:
        return {"sent": False, "error_category": "notification_too_large"}
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    opener = opener or urllib.request.urlopen
    try:
        with opener(request, timeout=8) as response:
            raw = response.read(4096)
            if response.status < 200 or response.status >= 300:
                return {"sent": False, "error_category": f"http_{response.status}"}
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"sent": False, "error_category": "invalid_response"}
        if result.get("code", 0) != 0:
            return {"sent": False, "error_category": f"feishu_code_{int(result.get('code', -1))}"}
        return {"sent": True, "error_category": None}
    except urllib.error.HTTPError as exc:
        return {"sent": False, "error_category": f"http_{exc.code}"}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"sent": False, "error_category": "network_or_timeout"}




def dispatch_alert(ledger, incident_id, credentials, text, *, sender=send_alert, now=None):
    if not credentials:
        return {"sent": False, "error_category": "notification_not_configured"}
    if not ledger.should_send(incident_id, now=now):
        return {"sent": False, "error_category": "deduplicated"}
    result = sender(credentials, text, now=now)
    ledger.record(incident_id, sent=bool(result.get("sent")), now=now)
    return {"sent": bool(result.get("sent")), "error_category": result.get("error_category")}


def incident_id_for(*, task, category, blocker):
    material = "\\0".join((_safe_text(task, 100), _safe_text(category, 80), _safe_text(blocker, 500)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _now(now):
    return now or dt.datetime.now(dt.timezone.utc)


def _parse_time(value):
    try:
        return dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


class NotificationLedger:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _read(self):
        if not self.path.exists():
            return {"schema_version": 1, "incidents": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("schema_version") != 1 or not isinstance(data.get("incidents"), dict):
                raise NotificationError("unsupported notification ledger")
            return data
        except (OSError, json.JSONDecodeError) as exc:
            raise NotificationError("notification ledger is unreadable") from exc

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, self.path)

    def should_send(self, incident_id, now=None):
        data = self._read()
        item = data["incidents"].get(incident_id)
        if not item or item.get("resolved"):
            return True
        attempts = int(item.get("attempts", 0))
        if item.get("sent"):
            reminders = int(item.get("reminders", 0))
            if reminders >= 2:
                return False
            last = _parse_time(item.get("last_sent_at"))
            return last is None or (_now(now) - last).total_seconds() >= 24 * 3600
        if attempts >= 5:
            return False
        next_time = _parse_time(item.get("next_attempt_at"))
        return next_time is None or _now(now) >= next_time

    def record(self, incident_id, *, sent, now=None):
        timestamp = _now(now)
        data = self._read()
        item = data["incidents"].setdefault(incident_id, {"attempts": 0, "sent": False, "reminders": 0})
        item["attempts"] = int(item.get("attempts", 0)) + 1
        if sent:
            if item.get("sent"):
                item["reminders"] = int(item.get("reminders", 0)) + 1
            item["sent"] = True
            item["last_sent_at"] = timestamp.isoformat()
            item.pop("next_attempt_at", None)
        else:
            seconds = min(15 * 60 * (2 ** (item["attempts"] - 1)), 6 * 3600)
            item["next_attempt_at"] = (timestamp + dt.timedelta(seconds=seconds)).isoformat()
        self._write(data)

    def resolve(self, incident_id, now=None):
        data = self._read()
        item = data["incidents"].setdefault(incident_id, {})
        item["resolved"] = True
        item["resolved_at"] = _now(now).isoformat()
        self._write(data)


def _credential_api():
    if os.name != "nt":
        raise NotificationError("Windows Credential Manager is available only on Windows")
    advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]
    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR), ("LastWritten", FILETIME), ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
        ]
    advapi.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    advapi.CredWriteW.restype = wintypes.BOOL
    advapi.CredReadW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
    ]
    advapi.CredReadW.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    advapi.CredFree.restype = None
    advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi.CredDeleteW.restype = wintypes.BOOL
    return advapi, CREDENTIALW


class WindowsCredentialStore:
    def write(self, credentials):
        if not valid_webhook(credentials.get("webhook_url", "")) or not credentials.get("signing_secret"):
            raise NotificationError("invalid Feishu webhook or signing secret")
        advapi, CREDENTIALW = _credential_api()
        blob = json.dumps(credentials, separators=(",", ":")).encode("utf-8")
        if len(blob) > 2400:
            raise NotificationError("notification credential is too large")
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        item = CREDENTIALW(
            Flags=0, Type=1, TargetName=TARGET, Comment="ShuLiu Dev Autopilot Feishu notification",
            LastWritten=None, CredentialBlobSize=len(blob),
            CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
            Persist=2, AttributeCount=0, Attributes=None, TargetAlias=None, UserName="webhook",
        )
        if not advapi.CredWriteW(ctypes.byref(item), 0):
            raise NotificationError("Windows Credential Manager could not save the notification credential")

    def read(self):
        advapi, CREDENTIALW = _credential_api()
        pointer = ctypes.POINTER(CREDENTIALW)()
        if not advapi.CredReadW(TARGET, 1, 0, ctypes.byref(pointer)):
            return None
        try:
            size = int(pointer.contents.CredentialBlobSize)
            if size <= 0 or size > 2400:
                raise NotificationError("stored notification credential is invalid")
            raw = ctypes.string_at(pointer.contents.CredentialBlob, size)
            data = json.loads(raw.decode("utf-8"))
            if not valid_webhook(data.get("webhook_url", "")) or not data.get("signing_secret"):
                raise NotificationError("stored notification credential is invalid")
            return data
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NotificationError("stored notification credential is invalid") from exc
        finally:
            advapi.CredFree(pointer)

    def delete(self):
        if os.name != "nt":
            return
        advapi, _ = _credential_api()
        advapi.CredDeleteW(TARGET, 1, 0)


def configure_interactive():
    try:
        url = getpass.getpass("飞书自定义机器人的 Webhook 地址（输入不回显）：")
        secret = getpass.getpass("机器人签名密钥（输入不回显）：")
        confirmation = getpass.getpass("再次输入签名密钥确认：")
        if not hmac.compare_digest(secret, confirmation):
            print("两次签名密钥不一致；未保存。")
            return 2
        WindowsCredentialStore().write({"webhook_url": url, "signing_secret": secret})
        print("通知凭据已保存在当前 Windows 用户的凭据管理器中。")
        if getpass.getpass("发送一条测试通知？输入 SEND 才会发送：") == "SEND":
            result = send_alert(
                WindowsCredentialStore().read(),
                "ShuLiu Autopilot：飞书阻塞通知通道测试成功。",
            )
            print("测试通知已发送。" if result["sent"] else f"测试通知未发送（{result['error_category']}）。")
        else:
            print("没有发送测试通知。")
        return 0
    except NotificationError as exc:
        print(f"通知设置失败：{exc}")
        return 2


if __name__ == "__main__":
    sys.exit(configure_interactive())
