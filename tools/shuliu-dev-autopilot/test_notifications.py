from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path
import notifications


class NotificationTests(unittest.TestCase):
    def test_webhook_is_restricted_to_official_feishu_hook(self):
        self.assertTrue(notifications.valid_webhook("https://open.feishu.cn/open-apis/bot/v2/hook/abc123"))
        for url in (
            "http://open.feishu.cn/open-apis/bot/v2/hook/abc",
            "https://evil.example/open-apis/bot/v2/hook/abc",
            "https://open.feishu.cn/open-apis/bot/v2/hook/abc?x=secret",
            "https://user:pass@open.feishu.cn/open-apis/bot/v2/hook/abc",
        ):
            self.assertFalse(notifications.valid_webhook(url), url)

    def test_signature_matches_feishu_hmac_sha256_scheme(self):
        secret = "signing-secret"
        timestamp = 1_700_000_000
        payload = notifications.signed_payload("ShuLiu Autopilot needs attention", secret, timestamp)
        signing_key = f"{timestamp}\n{secret}".encode()
        expected = base64.b64encode(hmac.new(signing_key, b"", hashlib.sha256).digest()).decode()
        self.assertEqual(payload["timestamp"], str(timestamp))
        self.assertEqual(payload["sign"], expected)
        self.assertEqual(payload["msg_type"], "text")

    def test_alert_uses_only_allowlisted_fields_and_contains_resume_instructions(self):
        text = notifications.format_alert(
            category="github_auth",
            task="Task 28",
            attempt_summary="read-only remote check reached HTTP auth",
            user_action="Sign in with the existing Windows Git Credential Manager.",
            resume="After signing in, the background check will verify read access and resume.",
            untrusted_log="DEEPSEEK_API_KEY=sk-secret",
        )
        self.assertIn("Task 28", text)
        self.assertIn("唯一需要做的事", text)
        self.assertIn("如何恢复", text)
        self.assertNotIn("sk-secret", text)
        self.assertNotIn("untrusted_log", text)

    def test_unknown_alert_category_fails_closed(self):
        with self.assertRaises(notifications.NotificationError):
            notifications.format_alert(category="ordinary_test_failure", task="Task 28")

    def test_notification_ledger_deduplicates_and_reminds_boundedly(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = notifications.NotificationLedger(Path(d) / "notifications.json")
            now = datetime(2026, 10, 21, tzinfo=timezone.utc)
            self.assertTrue(ledger.should_send("incident-1", now))
            ledger.record("incident-1", sent=True, now=now)
            self.assertFalse(ledger.should_send("incident-1", now))
            self.assertFalse(ledger.should_send("incident-1", now.replace(hour=1)))
            self.assertTrue(ledger.should_send("incident-1", now.replace(day=22)))
            ledger.record("incident-1", sent=True, now=now.replace(day=22))
            self.assertTrue(ledger.should_send("incident-1", now.replace(day=23)))
            ledger.record("incident-1", sent=True, now=now.replace(day=23))
            self.assertFalse(ledger.should_send("incident-1", now.replace(day=24)))


    def test_dispatch_deduplicates_incidents_and_keeps_only_safe_status(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=notifications.NotificationLedger(Path(d)/"notifications.json")
            calls=[]
            sender=lambda credentials,text,now=None: calls.append((credentials,text)) or {"sent":True,"error_category":None}
            message=notifications.format_alert(category="repair_limit",task="Task 28",
                attempt_summary="3 bounded repair rounds failed",
                user_action="Review the recorded local failure.",
                resume="Use the local resume control after resolving it.")
            result=notifications.dispatch_alert(ledger,"incident-a",{"webhook_url":"safe","signing_secret":"safe"},message,
                                                sender=sender,now=datetime(2026,10,21,tzinfo=timezone.utc))
            duplicate=notifications.dispatch_alert(ledger,"incident-a",{"webhook_url":"safe","signing_secret":"safe"},message,
                                                    sender=sender,now=datetime(2026,10,21,tzinfo=timezone.utc))
            self.assertTrue(result["sent"])
            self.assertEqual(duplicate["error_category"],"deduplicated")
            self.assertEqual(len(calls),1)

    def test_sender_never_logs_webhook_or_response_body(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, size=-1): return b'{"code":0,"msg":"ok"}'
        captured = {}
        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data)
            return Response()
        result = notifications.send_alert(
            {"webhook_url":"https://open.feishu.cn/open-apis/bot/v2/hook/secret-id","signing_secret":"sign-secret"},
            "ShuLiu Autopilot test notification", opener=opener, now=1_700_000_000)
        self.assertTrue(result["sent"])
        self.assertEqual(result["error_category"], None)
        self.assertIn("secret-id", captured["url"])
        self.assertNotIn("sign-secret", json.dumps(captured["body"]))
        self.assertNotIn("webhook_url", result)
        self.assertNotIn("response_body", result)


if __name__ == "__main__":
    unittest.main()
