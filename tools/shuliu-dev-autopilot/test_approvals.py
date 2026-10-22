import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
import approvals


def _reserve_from_process(ledger_path, request_id, gate, results):
    ledger=approvals.ApprovalLedger(Path(ledger_path))
    gate.wait(timeout=10)
    grant=ledger.reserve(request_id,task="Task 28",action="codex_service_recovery",scope="one retry")
    results.put(grant is not None)


class ApprovalTests(unittest.TestCase):
    def test_external_condition_change_alone_never_grants_paid_action(self):
        self.assertFalse(approvals.can_resume_external_action(
            {"action":"provider_request","status":"pending"},
            {"secret_configured":True,"budget_available":True,"canary_enabled":True},
        ))

    def test_exact_one_time_grant_is_bound_to_task_action_scope_and_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=approvals.ApprovalLedger(Path(d)/"approvals.json")
            now=datetime(2026,10,21,tzinfo=timezone.utc)
            request=ledger.create_request(
                task="Task 28",action="provider_request",scope="deepseek/deepseek-flash:Reply with exactly OK.:8 tokens",
                now=now,expires_minutes=20)
            self.assertFalse(ledger.is_approved(request["request_id"],now=now))
            self.assertTrue(ledger.approve(request["request_id"],confirmation=request["confirmation"],now=now))
            self.assertTrue(ledger.is_approved(request["request_id"],now=now))
            self.assertFalse(ledger.is_approved(request["request_id"],task="Task 29",now=now))
            self.assertFalse(ledger.is_approved(request["request_id"],action="deploy",now=now))
            self.assertFalse(ledger.is_approved(request["request_id"],scope="deepseek-other",now=now))
            self.assertFalse(ledger.is_approved(request["request_id"],now=now+timedelta(minutes=21)))

    def test_reservation_is_consumed_before_side_effect_and_unknown_outcome_never_replays(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=approvals.ApprovalLedger(Path(d)/"approvals.json")
            now=datetime(2026,10,21,tzinfo=timezone.utc)
            request=ledger.create_request(task="Task 28",action="provider_request",scope="exact-canary",now=now)
            ledger.approve(request["request_id"],confirmation=request["confirmation"],now=now)
            grant=ledger.reserve(request["request_id"],task="Task 28",action="provider_request",scope="exact-canary",now=now)
            self.assertIsNotNone(grant)
            self.assertIsNone(ledger.reserve(request["request_id"],task="Task 28",action="provider_request",scope="exact-canary",now=now))
            self.assertEqual(ledger.get(request["request_id"])["status"],"OUTCOME_UNKNOWN")
            self.assertFalse(approvals.can_resume_external_action(
                ledger.get(request["request_id"]),{"secret_configured":True}))

    def test_concurrent_reservations_consume_one_single_use_approval(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=approvals.ApprovalLedger(Path(d)/"approvals.json")
            request=ledger.create_request(task="Task 28",action="codex_service_recovery",scope="one retry")
            ledger.approve(request["request_id"],confirmation=request["confirmation"])
            start=Barrier(8)
            def reserve_once(_):
                start.wait(timeout=5)
                return ledger.reserve(request["request_id"],task="Task 28",action="codex_service_recovery",scope="one retry")
            with ThreadPoolExecutor(max_workers=8) as pool:
                results=list(pool.map(reserve_once,range(8)))
            self.assertEqual(sum(result is not None for result in results),1)
            self.assertEqual(ledger.get(request["request_id"])["uses"],1)

    def test_separate_processes_consume_only_one_approval(self):
        import multiprocessing
        with tempfile.TemporaryDirectory() as d:
            ledger=approvals.ApprovalLedger(Path(d)/"approvals.json")
            request=ledger.create_request(task="Task 28",action="codex_service_recovery",scope="one retry")
            ledger.approve(request["request_id"],confirmation=request["confirmation"])
            ctx=multiprocessing.get_context("spawn")
            gate=ctx.Barrier(2)
            results=ctx.Queue()
            workers=[ctx.Process(target=_reserve_from_process,args=(str(ledger.path),request["request_id"],gate,results))
                     for _ in range(2)]
            for worker in workers:worker.start()
            consumed=[results.get(timeout=15) for _ in workers]
            for worker in workers:worker.join(timeout=15)
            for worker in workers:
                if worker.is_alive():worker.terminate();worker.join(timeout=5)
            self.assertTrue(all(worker.exitcode==0 for worker in workers))
            self.assertEqual(sum(consumed),1)
            self.assertEqual(ledger.get(request["request_id"])["uses"],1)


    def test_approval_requires_interactive_local_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=approvals.ApprovalLedger(Path(d)/"approvals.json")
            request=ledger.create_request(task="Task 28",action="provider_request",scope="one canary")
            with self.assertRaises(approvals.ApprovalError):
                approvals.approve_pending_interactive(ledger,is_tty=False,input_fn=lambda _:request["confirmation"])
            self.assertEqual(ledger.get(request["request_id"])["status"],"PENDING")

    def test_interactive_approval_requires_exact_scoped_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=approvals.ApprovalLedger(Path(d)/"approvals.json")
            request=ledger.create_request(task="Task 28",action="provider_request",scope="one canary")
            shown=[]
            self.assertEqual(approvals.approve_pending_interactive(
                ledger,is_tty=True,input_fn=lambda prompt:shown.append(prompt) or request["confirmation"],
                output_fn=lambda value:shown.append(value)),1)
            self.assertEqual(ledger.get(request["request_id"])["status"],"APPROVED")
            self.assertTrue(any("one canary" in line for line in shown))

    def test_stop_published_during_confirmation_keeps_approval_pending(self):
        import autopilot
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);ledger=approvals.ApprovalLedger(root/"approvals.json")
            request=ledger.create_request(task="Task 28",action="security_review",scope="one review")
            def confirm_after_stopping(_prompt):
                self.assertTrue(autopilot.request_emergency_stop(root))
                return request["confirmation"]
            with self.assertRaises(approvals.ApprovalError):
                approvals.approve_pending_interactive(ledger,is_tty=True,
                    input_fn=confirm_after_stopping,output_fn=lambda _line:None)
            self.assertEqual(ledger.get(request["request_id"])["status"],"PENDING")

    def test_codex_recovery_approval_requires_explicit_service_restored_attestation(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=approvals.ApprovalLedger(Path(d)/"approvals.json")
            request=ledger.create_request(task="Task 28",action="codex_service_recovery",scope="one safe local retry")
            self.assertIn("CODEX SERVICE RESTORED",request["confirmation"])
            self.assertFalse(ledger.approve(request["request_id"],confirmation="APPROVE ONCE "+request["request_id"]))
            self.assertTrue(ledger.approve(request["request_id"],confirmation=request["confirmation"]))
            saved=ledger.get(request["request_id"])
            self.assertTrue(approvals.can_resume_external_action(saved,{"codex_service_restored":True}))
            self.assertFalse(approvals.can_resume_external_action(saved,{"codex_service_restored":False}))

    def test_codex_recovery_external_state_alone_is_not_human_attestation(self):
        self.assertFalse(approvals.can_resume_external_action(
            {"action":"codex_service_recovery","status":"APPROVED"},
            {"codex_service_restored":True}))

    def test_failed_operation_is_not_automatically_retried(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=approvals.ApprovalLedger(Path(d)/"approvals.json")
            now=datetime(2026,10,21,tzinfo=timezone.utc)
            request=ledger.create_request(task="Task 28",action="provider_request",scope="one",now=now)
            ledger.approve(request["request_id"],confirmation=request["confirmation"],now=now)
            ledger.reserve(request["request_id"],task="Task 28",action="provider_request",scope="one",now=now)
            ledger.finish(request["request_id"],outcome="failed",now=now)
            self.assertEqual(ledger.get(request["request_id"])["status"],"FAILED")
            self.assertIsNone(ledger.reserve(request["request_id"],task="Task 28",action="provider_request",scope="one",now=now))


if __name__ == "__main__":
    unittest.main()
