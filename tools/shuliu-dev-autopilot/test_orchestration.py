import json
import tempfile
import unittest
from pathlib import Path
import autopilot
import orchestration


class OrchestrationTests(unittest.TestCase):
    def test_approval_request_is_persisted_as_waiting_approval_without_secret_in_event(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            state=autopilot.new_state("b"*40)
            autopilot.transition(state,"RUNNING")
            autopilot.save_state(root/"state.json",state)
            request=orchestration.queue_approval(root,task="Task 28",action="provider_request",
                scope="deepseek/deepseek-flash; one request; 8 tokens")
            saved=autopilot.load_state(root/"state.json")
            self.assertEqual(saved["status"],"WAITING_APPROVAL")
            self.assertIsNone(saved["approved_resume"])
            self.assertEqual(len((root/"events.jsonl").read_text(encoding="utf-8").splitlines()),2)
            self.assertNotIn("api_key", (root/"events.jsonl").read_text(encoding="utf-8").lower())

    def test_expired_repair_resume_can_be_reissued_without_widening_scope(self):
        from datetime import datetime, timedelta, timezone
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            state=autopilot.new_state("c"*40)
            autopilot.transition(state,"RUNNING")
            autopilot.save_state(root/"state.json",state)
            scope="One safe local verification after three repair rounds; suite=autopilot self-tests"
            now=datetime(2026,10,22,tzinfo=timezone.utc)
            with patch("runtime.notify_blocker",return_value={"sent":False,"error_category":"notification_not_configured"}):
                old=orchestration.queue_approval(root,task="Task 28",action="repair_resume",
                    scope=scope,resume_kind="safe_local_verification",expires_minutes=1,now=now)
                renewed=orchestration.queue_approval(root,task="Task 28",action="repair_resume",
                    scope=scope,resume_kind="safe_local_verification",expires_minutes=60,now=now+timedelta(minutes=2))
            ledger=orchestration.ApprovalLedger(root/"approvals.json")
            self.assertNotEqual(old["request_id"],renewed["request_id"])
            self.assertEqual(ledger.get(old["request_id"])["status"],"EXPIRED")
            self.assertEqual(ledger.get(renewed["request_id"])["status"],"PENDING")
            saved=autopilot.load_state(root/"state.json")
            self.assertEqual(saved["status"],"WAITING_APPROVAL")
            self.assertEqual(saved["approval_request_id"],renewed["request_id"])
            self.assertEqual(saved["approved_resume"]["scope"],scope)

    def test_unknown_repair_resume_outcome_cannot_be_renewed(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            state=autopilot.new_state("d"*40)
            autopilot.transition(state,"RUNNING")
            autopilot.save_state(root/"state.json",state)
            scope="One safe local verification after three repair rounds; suite=autopilot self-tests"
            with patch("runtime.notify_blocker",return_value={"sent":False,"error_category":"notification_not_configured"}):
                request=orchestration.queue_approval(root,task="Task 28",action="repair_resume",
                    scope=scope,resume_kind="safe_local_verification")
            ledger=orchestration.ApprovalLedger(root/"approvals.json")
            self.assertTrue(ledger.approve(request["request_id"],confirmation=request["confirmation"]))
            ledger.reserve(request["request_id"],task="Task 28",action="repair_resume",scope=scope)
            with patch("runtime.notify_blocker",return_value={"sent":False,"error_category":"notification_not_configured"}):
                with self.assertRaises(orchestration.OrchestrationError):
                    orchestration.queue_approval(root,task="Task 28",action="repair_resume",
                        scope=scope,resume_kind="safe_local_verification")
            self.assertEqual(ledger.pending(),[])

    def test_repair_resume_requires_an_allowlisted_suite_in_its_scope(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            state=autopilot.new_state("e"*40)
            autopilot.transition(state,"RUNNING")
            autopilot.save_state(root/"state.json",state)
            with patch("autopilot.safe_test_commands",return_value=[("autopilot self-tests",["python"])]), \
                 patch("runtime.notify_blocker",return_value={"sent":False,"error_category":"notification_not_configured"}):
                with self.assertRaises(orchestration.OrchestrationError):
                    orchestration.queue_approval(root,task="Task 28",action="repair_resume",
                        scope="One safe local verification after three repair rounds; suite=unlisted suite",
                        resume_kind="safe_local_verification")
            self.assertEqual(orchestration.ApprovalLedger(root/"approvals.json").pending(),[])

    def test_approval_cannot_target_another_task_or_unsafe_resume_kind(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            state=autopilot.new_state("c"*40)
            autopilot.save_state(root/"state.json",state)
            with self.assertRaises(orchestration.OrchestrationError):
                orchestration.queue_approval(root,task="Task 29",action="provider_request",scope="one")
            with self.assertRaises(orchestration.OrchestrationError):
                orchestration.queue_approval(root,task="Task 28",action="provider_request",scope="one",
                                             resume_kind="provider_request")


if __name__ == "__main__":
    unittest.main()




class CodexEngineRecoveryTests(unittest.TestCase):
    def test_repair_mcp_failure_enters_approval_wait_without_applying_patch(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            state=autopilot.new_state("f"*40)
            state["run_id"]="repair-test"
            autopilot.transition(state,"RUNNING")
            autopilot.transition(state,"VERIFYING")
            autopilot.save_state(root/"state.json",state)
            failure={"timed_out":False,"output_tail":"unit test failed"}
            with patch("autopilot._codex",return_value=(1,"MCP startup failed during handshake","codex_mcp_unavailable")):
                self.assertFalse(autopilot._repair("autopilot self-tests",["python","-m","unittest"],
                    failure,state,root,set(),remaining=30))
            saved=autopilot.load_state(root/"state.json")
            self.assertEqual(saved["status"],"WAITING_APPROVAL")
            self.assertEqual(saved["repair_count"],1)
            self.assertEqual(saved["last_failure"]["failure_stage"],"mcp_initialization_handshake")
            self.assertFalse(saved.get("local_changes_from_repair"))
            self.assertEqual(orchestration.ApprovalLedger(root/"approvals.json").get(
                saved["approval_request_id"])["action"],"codex_service_recovery")
            events=(root/"events.jsonl").read_text(encoding="utf-8")
            self.assertIn("codex_service_recovery_waiting_approval",events)
            self.assertNotIn("chatgpt.com",events)

    def test_mcp_unavailable_is_persisted_as_waiting_approval_for_safe_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            state=autopilot.new_state("d"*40)
            autopilot.transition(state,"RUNNING")
            autopilot.transition(state,"VERIFYING")
            autopilot.save_state(root/"state.json",state)
            self.assertTrue(autopilot.pause_for_codex_recovery(
                state,root,suite="autopilot self-tests",exit_code=1,
                error_category="codex_mcp_unavailable"))
            saved=autopilot.load_state(root/"state.json")
            request=orchestration.ApprovalLedger(root/"approvals.json").get(saved["approval_request_id"])
            self.assertEqual(saved["status"],"WAITING_APPROVAL")
            self.assertTrue(saved["requires_user_approval"])
            self.assertEqual(request["action"],"codex_service_recovery")
            self.assertEqual(request["status"],"PENDING")
            self.assertEqual(saved["approved_resume"]["kind"],"safe_local_verification")
            events=(root/"events.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("chatgpt.com",events)
            self.assertNotIn("token secret",events)

    def test_generic_codex_failure_does_not_request_service_recovery_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            state=autopilot.new_state("e"*40)
            autopilot.transition(state,"RUNNING")
            autopilot.transition(state,"VERIFYING")
            autopilot.save_state(root/"state.json",state)
            self.assertFalse(autopilot.pause_for_codex_recovery(
                state,root,suite="Task 28 local test",exit_code=1,
                error_category="codex_cli_failed"))
            self.assertEqual(autopilot.load_state(root/"state.json")["status"],"VERIFYING")
            self.assertEqual(orchestration.ApprovalLedger(root/"approvals.json").pending(),[])

class NotificationApprovalRecoveryIntegrationTests(unittest.TestCase):
    def test_blocker_notice_single_approval_and_scoped_recovery_with_fake_transport(self):
        import json
        import sys
        from unittest.mock import patch
        import notifications
        import scheduler
        from approvals import ApprovalLedger, approve_pending_interactive

        class FakeCredentialStore:
            def read(self):
                return {
                    "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/test-only-id",
                    "signing_secret": "synthetic-signing-secret-for-tests",
                }

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, size=-1):
                return b'{"code":0,"msg":"ok"}'

        sent_requests = []

        def fake_transport(request, timeout):
            sent_requests.append((request, timeout))
            return Response()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = autopilot.new_state("a" * 40)
            state["repair_count"] = 3
            state["max_repair_rounds"] = 3
            autopilot.transition(state, "RUNNING")
            autopilot.save_state(root / "state.json", state)
            scope = "One safe local verification after three repair rounds; suite=autopilot self-tests"
            suite = ("autopilot self-tests", [sys.executable, "-c", "print('passed')"])
            completed = {"exit_code": 0, "output": "passed", "timed_out": False, "terminated": True}

            with patch("notifications.WindowsCredentialStore", FakeCredentialStore), \
                 patch("notifications.urllib.request.urlopen", side_effect=fake_transport), \
                 patch("autopilot.safe_test_commands", return_value=[suite]), \
                 patch("autopilot.run_bounded_process", return_value=completed):
                request = orchestration.queue_approval(
                    root, task="Task 28", action="repair_resume", scope=scope,
                    resume_kind="safe_local_verification")
                repeated = orchestration.queue_approval(
                    root, task="Task 28", action="repair_resume", scope=scope,
                    resume_kind="safe_local_verification")

                self.assertEqual(request["request_id"], repeated["request_id"])
                self.assertEqual(autopilot.load_state(root / "state.json")["status"], "WAITING_APPROVAL")
                self.assertEqual(len(sent_requests), 1, "duplicate blocker must not send a second notice")
                payload = json.loads(sent_requests[0][0].data.decode("utf-8"))
                self.assertIn("Task 28", payload["content"]["text"])
                self.assertIn("唯一需要做的事", payload["content"]["text"])
                self.assertNotIn("synthetic-signing-secret-for-tests", json.dumps(payload))
                self.assertEqual(sent_requests[0][1], 8)

                approved = approve_pending_interactive(
                    ApprovalLedger(root / "approvals.json"),
                    request_ids=[request["request_id"]],
                    is_tty=True,
                    input_fn=lambda _prompt: request["confirmation"],
                    output_fn=lambda _line: None,
                )
                self.assertEqual(approved, 1)
                result = scheduler.tick(root)

            self.assertTrue(result["ran"])
            self.assertTrue(result["verification_succeeded"])
            self.assertEqual(result["status"], "BLOCKED", "local verification cannot close Task 28 gates")
            saved = autopilot.load_state(root / "state.json")
            self.assertEqual(saved["local_verification"], "PASSED")
            self.assertEqual([item["name"] for item in saved["test_results"]], ["autopilot self-tests"])
            record = ApprovalLedger(root / "approvals.json").get(request["request_id"])
            self.assertEqual(record["status"], "SUCCEEDED")
            self.assertEqual(record["uses"], 1)
