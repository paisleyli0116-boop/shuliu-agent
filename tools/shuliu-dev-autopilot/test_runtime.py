import tempfile
import unittest
from pathlib import Path
import autopilot
import runtime


class RuntimeTests(unittest.TestCase):
    def test_missing_feishu_credentials_never_attempts_network(self):
        with tempfile.TemporaryDirectory() as d:
            class Store:
                def read(self): return None
            result=runtime.notify_blocker(Path(d),task="Task 28",category="provider_input",
                blocker="key needed",attempt_summary="no request sent",
                user_action="Enter the key in Desktop settings.",
                resume="The scheduler will recheck local readiness.",
                credential_store=Store(),sender=lambda *a,**k:self.fail("network sender called"))
            self.assertEqual(result["error_category"],"notification_not_configured")

    def test_notification_logs_only_safe_delivery_status(self):
        with tempfile.TemporaryDirectory() as d:
            class Store:
                def read(self): return {"webhook_url":"https://open.feishu.cn/open-apis/bot/v2/hook/secret-id",
                                        "signing_secret":"private-signing-secret"}
            sent=[]
            result=runtime.notify_blocker(Path(d),task="Task 28",category="provider_input",
                blocker="needs input",attempt_summary="preflight complete",
                user_action="Enter the key locally.",
                resume="Autopilot checks the checkpoint after configuration.",
                credential_store=Store(),
                sender=lambda credentials,text,now=None:sent.append((credentials,text)) or {"sent":True,"error_category":None})
            self.assertTrue(result["sent"])
            log=(Path(d)/"events.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("private-signing-secret",log)
            self.assertNotIn("secret-id",log)
            self.assertNotIn(sent[0][1],log)


if __name__ == "__main__":
    unittest.main()
