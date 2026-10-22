import tempfile
import unittest
import time
import multiprocessing
import json
from pathlib import Path
from unittest.mock import Mock
import approvals
import autopilot
import scheduler


def _run_killable_scheduled_recovery(state_dir, started, effect_path):
    def effect_runner(directory):
        with Path(effect_path).open("a",encoding="utf-8") as stream:
            stream.write("effect\n")
            stream.flush()
        started.set()
        while True:
            time.sleep(0.1)
    scheduler.tick(Path(state_dir),runner=effect_runner)

def _hold_scheduler_recovery_lock(state_dir, started, release, result_queue):
    def runner(directory):
        started.set()
        if not release.wait(timeout=15):
            raise RuntimeError("test release event timed out")
        return 0
    result_queue.put(scheduler.tick(Path(state_dir),runner=runner))


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write_state(self, status, **extra):
        state = autopilot.new_state("a" * 40)
        state["status"] = status
        state.update(extra)
        autopilot.save_state(self.directory / "state.json", state)
        return state

    def test_blocked_task_does_not_rerun_or_repeat_side_effect(self):
        self.write_state("BLOCKED")
        runner = Mock()
        result = scheduler.tick(self.directory, runner=runner)
        self.assertFalse(result["ran"])
        runner.assert_not_called()

    def test_non_recovery_approval_action_has_no_automatic_resume_handler(self):
        ledger = approvals.ApprovalLedger(self.directory / "approvals.json")
        request = ledger.create_request(task="Task 28", action="security_review",
                                        scope="resume-safe-local-checks")
        approvals.approve_pending_interactive(
            ledger,request_ids=[request["request_id"]],is_tty=True,
            input_fn=lambda prompt:request["confirmation"],output_fn=lambda line:None)
        self.write_state("WAITING_APPROVAL", approved_resume={
            "kind": "safe_local_verification", "request_id": request["request_id"],
            "action": "security_review", "scope": "resume-safe-local-checks",
        })
        runner = Mock(return_value=0)
        result = scheduler.tick(self.directory, runner=runner)
        self.assertFalse(result["ran"])
        runner.assert_not_called()
        self.assertEqual(result["status"], "WAITING_APPROVAL")
        consumed=ledger.get(request["request_id"])
        self.assertEqual(consumed["status"],"APPROVED")
        self.assertEqual(consumed["uses"],0)

    def test_repair_limit_approval_resumes_only_one_local_verification(self):
        from unittest.mock import patch
        import orchestration
        state=autopilot.new_state("c"*40)
        autopilot.transition(state,"RUNNING")
        autopilot.save_state(self.directory/"state.json",state)
        with patch("runtime.notify_blocker",return_value={"sent":False,"error_category":"notification_not_configured"}):
            request=orchestration.queue_approval(self.directory,task="Task 28",action="repair_resume",
                scope="One safe local verification after three repair rounds; suite=autopilot self-tests",
                resume_kind="safe_local_verification",expires_minutes=60)
        ledger=approvals.ApprovalLedger(self.directory/"approvals.json")
        self.assertEqual(scheduler.tick(self.directory,runner=Mock())["status"],"WAITING_APPROVAL")
        self.assertTrue(ledger.approve(request["request_id"],confirmation=request["confirmation"]))
        calls=[]
        def safe_retest(directory):
            calls.append(directory)
            resumed=autopilot.load_state(directory/"state.json")
            suite=resumed["approved_resume"]["test_suite"]
            request_id=resumed["approved_resume"]["request_id"]
            resumed["run_id"]="safe-retest-run"
            resumed["approval_resume_in_flight"]["run_id"]=resumed["run_id"]
            resumed["authorized_verification_request_id"]=request_id
            resumed["authorized_verification_run_id"]=resumed["run_id"]
            resumed["test_results"]=[{"name":suite,"passed":True,"exit_code":0,
                "timed_out":False,"process_tree_terminated":True}]
            resumed["local_verification"]="PASSED"
            autopilot.transition(resumed,"RUNNING")
            autopilot.transition(resumed,"BLOCKED",blocking_reason="Task 28 gates still require evidence")
            autopilot.save_state(directory/"state.json",resumed)
            return 0
        result=scheduler.tick(self.directory,runner=safe_retest)
        self.assertEqual(result["status"],"BLOCKED")
        self.assertEqual(calls,[self.directory])
        used=ledger.get(request["request_id"])
        self.assertEqual(used["status"],"SUCCEEDED")
        self.assertEqual(used["uses"],1)
        self.assertFalse(scheduler.tick(self.directory,runner=safe_retest)["ran"])
        self.assertEqual(len(calls),1)

    def test_scheduler_passes_consumed_approval_id_to_run_cycle(self):
        from unittest.mock import patch
        import orchestration
        state=autopilot.new_state("d"*40)
        autopilot.transition(state,"RUNNING")
        autopilot.save_state(self.directory/"state.json",state)
        with patch("runtime.notify_blocker",return_value={"sent":False,"error_category":"notification_not_configured"}):
            request=orchestration.queue_approval(self.directory,task="Task 28",action="repair_resume",
                scope="One safe local verification after three repair rounds; suite=autopilot self-tests",
                resume_kind="safe_local_verification")
        ledger=approvals.ApprovalLedger(self.directory/"approvals.json")
        self.assertTrue(ledger.approve(request["request_id"],confirmation=request["confirmation"]))
        with patch("autopilot.run_cycle",return_value=0) as run:
            scheduler.tick(self.directory)
        run.assert_called_once()
        args,kwargs=run.call_args
        self.assertEqual(args,(self.directory,))
        self.assertEqual(autopilot._consume_resume_capability(kwargs["resume_capability"],self.directory),
                         request["request_id"])

    def test_repair_resume_is_verification_only_even_after_fingerprint_change(self):
        from unittest.mock import patch
        import orchestration
        sha="e"*40
        state=autopilot.new_state(sha)
        autopilot.transition(state,"RUNNING")
        autopilot.save_state(self.directory/"state.json",state)
        scope="One safe local verification after three repair rounds; suite=autopilot self-tests"
        with patch("runtime.notify_blocker",return_value={"sent":False,"error_category":"notification_not_configured"}):
            request=orchestration.queue_approval(self.directory,task="Task 28",action="repair_resume",
                scope=scope,resume_kind="safe_local_verification",expires_minutes=60)
        state=autopilot.load_state(self.directory/"state.json")
        state["repair_count"]=3
        state["workspace_fingerprint"]="old-workspace"
        state["tool_fingerprint"]="old-tools"
        autopilot.save_state(self.directory/"state.json",state)
        ledger=approvals.ApprovalLedger(self.directory/"approvals.json")
        self.assertTrue(ledger.approve(request["request_id"],confirmation=request["confirmation"]))
        with patch("autopilot.current_branch_and_sha",return_value=(autopilot.ALLOWED_BRANCH,sha)), \
             patch("autopilot.workspace_fingerprint",return_value="new-workspace"), \
             patch("autopilot.tool_fingerprint",return_value="new-tools"), \
             patch("autopilot.capture_dirty_snapshot",return_value={}), \
             patch("autopilot._status_paths",return_value=set()), \
             patch("autopilot._sync_plan_progress",return_value=(True,None)), \
             patch("autopilot._scope_check") as scope_check, \
             patch("autopilot.safe_test_commands",return_value=[("autopilot self-tests",["python","-c","pass"])]), \
             patch("autopilot._test",return_value={"name":"autopilot self-tests","passed":False,"timed_out":False,
                   "exit_code":1,"output_tail":"fixture failure"}), \
             patch("autopilot._repair",return_value=False) as repair:
            result=scheduler.tick(self.directory)
        saved=autopilot.load_state(self.directory/"state.json")
        self.assertEqual(result["status"],"WAITING_APPROVAL")
        self.assertEqual(saved["status"],"WAITING_APPROVAL")
        self.assertEqual(saved["repair_count"],3)
        self.assertEqual(saved["last_failure"]["repair_stopped_reason"],"approved verification failed; no repair authorized")
        repair.assert_not_called()
        scope_check.assert_not_called()
        self.assertEqual(ledger.get(request["request_id"])["status"],"FAILED")

    def test_repair_resume_runs_only_the_suite_bound_to_approval_scope(self):
        from unittest.mock import patch
        import orchestration
        state=autopilot.new_state("a"*40)
        autopilot.transition(state,"RUNNING")
        autopilot.save_state(self.directory/"state.json",state)
        scope="One safe local verification after three repair rounds; suite=autopilot self-tests"
        with patch("runtime.notify_blocker",return_value={"sent":False,"error_category":"notification_not_configured"}):
            request=orchestration.queue_approval(self.directory,task="Task 28",action="repair_resume",
                scope=scope,resume_kind="safe_local_verification",expires_minutes=60)
        ledger=approvals.ApprovalLedger(self.directory/"approvals.json")
        self.assertTrue(ledger.approve(request["request_id"],confirmation=request["confirmation"]))
        calls=[]
        suites=[("autopilot self-tests",["python","-c","pass"]),
                ("Task 28 Desktop settings and redaction fakes",["python","-c","pass"])]
        real_test=autopilot._test
        def passed_suite(label,command,directory,current,timeout):
            calls.append(label)
            return real_test(label,command,directory,current,timeout)
        with patch("autopilot.current_branch_and_sha",return_value=(autopilot.ALLOWED_BRANCH,"a"*40)), \
             patch("autopilot.workspace_fingerprint",return_value="current-workspace"), \
             patch("autopilot.tool_fingerprint",return_value="current-tools"), \
             patch("autopilot.capture_dirty_snapshot",return_value={}), \
             patch("autopilot._status_paths",return_value=set()), \
             patch("autopilot._sync_plan_progress",return_value=(True,None)), \
             patch("autopilot.safe_test_commands",return_value=suites), \
             patch("autopilot._test",side_effect=passed_suite), \
             patch("autopilot._notify_human"):
            result=scheduler.tick(self.directory)
        self.assertEqual(result["status"],"BLOCKED")
        self.assertEqual(calls,["autopilot self-tests"])
        self.assertEqual(ledger.get(request["request_id"])["status"],"SUCCEEDED")

    def test_codex_recovery_approval_runs_only_approved_suite_and_never_repairs(self):
        import orchestration
        from unittest.mock import patch
        state=autopilot.new_state("9"*40)
        autopilot.transition(state,"RUNNING")
        autopilot.transition(state,"VERIFYING")
        autopilot.save_state(self.directory/"state.json",state)
        scope="One safe local repair retry; suite=approved-suite; category=codex_mcp_unavailable; exit=1"
        with patch("runtime.notify_blocker",return_value={"sent":False,"error_category":"notification_not_configured"}), \
             patch("autopilot.safe_test_commands",return_value=[
                 ("approved-suite",["python","-c","pass"]),
                 ("unapproved-suite",["python","-c","pass"]),
             ]):
            request=orchestration.queue_approval(self.directory,task="Task 28",action="codex_service_recovery",
                scope=scope,resume_kind="safe_local_verification")
        ledger=approvals.ApprovalLedger(self.directory/"approvals.json")
        self.assertTrue(ledger.approve(request["request_id"],confirmation=request["confirmation"]))
        calls=[]
        real_test=autopilot._test
        def test_suite(label,command,directory,current,timeout):
            calls.append(label)
            return real_test(label,command,directory,current,timeout)
        with patch("autopilot.current_branch_and_sha",return_value=(autopilot.ALLOWED_BRANCH,"9"*40)), \
             patch("autopilot.workspace_fingerprint",return_value="current-workspace"), \
             patch("autopilot.tool_fingerprint",return_value="current-tools"), \
             patch("autopilot.capture_dirty_snapshot",return_value={}), \
             patch("autopilot._status_paths",return_value=set()), \
             patch("autopilot._sync_plan_progress",return_value=(True,None)), \
             patch("autopilot._scope_check") as scope_check, \
             patch("autopilot.safe_test_commands",return_value=[
                 ("approved-suite",["python","-c","pass"]),
                 ("unapproved-suite",["python","-c","pass"]),
             ]), \
             patch("autopilot._test",side_effect=test_suite), \
             patch("autopilot._repair",return_value=False) as repair, \
             patch("autopilot._notify_human"):
            result=scheduler.tick(self.directory)
        self.assertEqual(calls,["approved-suite"])
        self.assertEqual(result["status"],"BLOCKED")
        self.assertEqual(ledger.get(request["request_id"])["status"],"SUCCEEDED")
        repair.assert_not_called()
        scope_check.assert_not_called()

    def test_repair_resume_pause_before_tests_consumes_approval_without_success(self):
        from unittest.mock import patch
        import orchestration
        state=autopilot.new_state("f"*40)
        autopilot.transition(state,"RUNNING")
        autopilot.save_state(self.directory/"state.json",state)
        scope="One safe local verification after three repair rounds; suite=autopilot self-tests"
        with patch("runtime.notify_blocker",return_value={"sent":False,"error_category":"notification_not_configured"}):
            request=orchestration.queue_approval(self.directory,task="Task 28",action="repair_resume",
                scope=scope,resume_kind="safe_local_verification",expires_minutes=60)
        ledger=approvals.ApprovalLedger(self.directory/"approvals.json")
        self.assertTrue(ledger.approve(request["request_id"],confirmation=request["confirmation"]))
        original_run_cycle=autopilot.run_cycle
        def pause_after_reservation(directory,*,resume_capability=None,_held_lock=None):
            (self.directory/"PAUSE").write_text("pause",encoding="utf-8")
            return original_run_cycle(directory,resume_capability=resume_capability,_held_lock=_held_lock)
        with patch("autopilot.run_cycle",side_effect=pause_after_reservation):
            result=scheduler.tick(self.directory)
        self.assertEqual(result["status"],"WAITING_APPROVAL")
        self.assertEqual(result["exit_code"],1)
        self.assertEqual(ledger.get(request["request_id"])["status"],"FAILED")
        self.assertEqual(autopilot.load_state(self.directory/"state.json")["status"],"WAITING_APPROVAL")

    def test_resume_that_returns_zero_without_running_approved_suite_is_not_success(self):
        import orchestration
        state=autopilot.new_state("8"*40)
        autopilot.transition(state,"RUNNING")
        autopilot.save_state(self.directory/"state.json",state)
        scope="One safe local repair retry; suite=autopilot self-tests; category=codex_mcp_unavailable; exit=1"
        request=orchestration.queue_approval(self.directory,task="Task 28",action="codex_service_recovery",
            scope=scope,resume_kind="safe_local_verification")
        ledger=approvals.ApprovalLedger(self.directory/"approvals.json")
        self.assertTrue(ledger.approve(request["request_id"],confirmation=request["confirmation"]))
        result=scheduler.tick(self.directory,runner=Mock(return_value=0))
        self.assertFalse(result.get("verification_succeeded",False))
        self.assertEqual(ledger.get(request["request_id"])["status"],"FAILED")
        self.assertEqual(autopilot.load_state(self.directory/"state.json")["local_verification"],"NOT_RUN")

    def test_concurrent_scheduler_tick_cannot_overwrite_active_recovery_checkpoint(self):
        import orchestration
        state=autopilot.new_state("7"*40)
        autopilot.transition(state,"RUNNING")
        autopilot.save_state(self.directory/"state.json",state)
        scope="One safe local repair retry; suite=autopilot self-tests; category=codex_mcp_unavailable; exit=1"
        request=orchestration.queue_approval(self.directory,task="Task 28",action="codex_service_recovery",
            scope=scope,resume_kind="safe_local_verification")
        ledger=approvals.ApprovalLedger(self.directory/"approvals.json")
        self.assertTrue(ledger.approve(request["request_id"],confirmation=request["confirmation"]))
        ctx=multiprocessing.get_context("spawn")
        started=ctx.Event();release=ctx.Event();results=ctx.Queue()
        child=ctx.Process(target=_hold_scheduler_recovery_lock,
            args=(str(self.directory),started,release,results))
        child.start()
        try:
            self.assertTrue(started.wait(timeout=15),"first scheduler did not reach its runner")
            before=json.loads((self.directory/"state.json").read_text(encoding="utf-8"))
            second=scheduler.tick(self.directory,runner=Mock(return_value=0))
            after=json.loads((self.directory/"state.json").read_text(encoding="utf-8"))
            self.assertEqual(second.get("status"),"BUSY")
            self.assertFalse(second.get("ran",True))
            self.assertEqual(after,before,"concurrent tick changed the active recovery checkpoint")
        finally:
            release.set()
            child.join(timeout=15)
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
        self.assertFalse(child.is_alive())

    def test_concurrent_approval_writer_cannot_overwrite_active_recovery_checkpoint(self):
        import orchestration
        from unittest.mock import patch
        state=autopilot.new_state("6"*40)
        autopilot.transition(state,"RUNNING")
        autopilot.save_state(self.directory/"state.json",state)
        scope="One safe local repair retry; suite=autopilot self-tests; category=codex_mcp_unavailable; exit=1"
        with patch("runtime.notify_blocker",return_value={"sent":False,"error_category":"notification_not_configured"}):
            request=orchestration.queue_approval(self.directory,task="Task 28",action="codex_service_recovery",
                scope=scope,resume_kind="safe_local_verification")
        ledger=approvals.ApprovalLedger(self.directory/"approvals.json")
        self.assertTrue(ledger.approve(request["request_id"],confirmation=request["confirmation"]))
        ctx=multiprocessing.get_context("spawn")
        started=ctx.Event();release=ctx.Event();results=ctx.Queue()
        child=ctx.Process(target=_hold_scheduler_recovery_lock,
            args=(str(self.directory),started,release,results))
        child.start()
        try:
            self.assertTrue(started.wait(timeout=15),"recovery runner did not hold the checkpoint lock")
            before=json.loads((self.directory/"state.json").read_text(encoding="utf-8"))
            rejected=False
            with patch("runtime.notify_blocker",return_value={"sent":False,"error_category":"notification_not_configured"}):
                try:
                    orchestration.queue_approval(self.directory,task="Task 28",action="security_review",
                        scope="competing approval")
                except orchestration.OrchestrationError:
                    rejected=True
            after=json.loads((self.directory/"state.json").read_text(encoding="utf-8"))
            self.assertTrue(rejected,"a second approval writer must refuse while recovery owns the checkpoint")
            self.assertEqual(after,before,"competing approval changed the in-flight recovery checkpoint")
        finally:
            release.set()
            child.join(timeout=15)
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
        self.assertFalse(child.is_alive())

    def test_provider_request_approval_never_becomes_an_automatic_request_handler(self):
        ledger = approvals.ApprovalLedger(self.directory / "approvals.json")
        request = ledger.create_request(task="Task 28", action="provider_request", scope="one canary")
        ledger.approve(request["request_id"], confirmation=request["confirmation"])
        self.write_state("WAITING_APPROVAL", approved_resume={
            "kind": "provider_request", "request_id": request["request_id"],
            "action": "provider_request", "scope": "one canary",
        })
        runner = Mock()
        result = scheduler.tick(self.directory, runner=runner)
        self.assertFalse(result["ran"])
        runner.assert_not_called()

    def test_codex_service_recovery_waits_for_one_scoped_approval_then_stops_at_gate(self):
        import orchestration
        state=autopilot.new_state("b"*40)
        autopilot.transition(state,"RUNNING")
        autopilot.transition(state,"VERIFYING")
        autopilot.save_state(self.directory/"state.json",state)
        request=orchestration.queue_approval(self.directory,task="Task 28",action="codex_service_recovery",
            scope="One safe local repair retry; suite=autopilot self-tests; category=codex_mcp_unavailable; exit=1",
            resume_kind="safe_local_verification")
        runner=Mock()
        waiting=scheduler.tick(self.directory,runner=runner)
        self.assertFalse(waiting["ran"])
        runner.assert_not_called()
        ledger=approvals.ApprovalLedger(self.directory/"approvals.json")
        self.assertTrue(ledger.approve(request["request_id"],confirmation=request["confirmation"]))

        def finish_at_blocked_gate(directory):
            resumed=autopilot.load_state(directory/"state.json")
            suite=resumed["approved_resume"]["test_suite"]
            request_id=resumed["approved_resume"]["request_id"]
            resumed["run_id"]="codex-recovery-run"
            resumed["approval_resume_in_flight"]["run_id"]=resumed["run_id"]
            resumed["authorized_verification_request_id"]=request_id
            resumed["authorized_verification_run_id"]=resumed["run_id"]
            resumed["test_results"]=[{"name":suite,"passed":True,"exit_code":0,
                "timed_out":False,"process_tree_terminated":True}]
            resumed["local_verification"]="PASSED"
            autopilot.transition(resumed,"RUNNING")
            autopilot.transition(resumed,"BLOCKED",blocking_reason="acceptance gates remain closed")
            autopilot.save_state(directory/"state.json",resumed)
            return 0

        resumed=scheduler.tick(self.directory,runner=finish_at_blocked_gate)
        self.assertTrue(resumed["ran"])
        self.assertEqual(resumed["status"],"BLOCKED")
        used=ledger.get(request["request_id"])
        self.assertEqual(used["status"],"SUCCEEDED")
        self.assertEqual(used["uses"],1)
        stopped=scheduler.tick(self.directory,runner=runner)
        self.assertFalse(stopped["ran"])
        runner.assert_not_called()

    def test_codex_recovery_without_restored_attestation_remains_waiting(self):
        import json
        import orchestration
        state=autopilot.new_state("e"*40)
        autopilot.transition(state,"RUNNING")
        autopilot.transition(state,"VERIFYING")
        autopilot.save_state(self.directory/"state.json",state)
        request=orchestration.queue_approval(self.directory,task="Task 28",action="codex_service_recovery",
            scope="One safe local repair retry; suite=autopilot self-tests; category=codex_mcp_unavailable; exit=1",
            resume_kind="safe_local_verification")
        ledger=approvals.ApprovalLedger(self.directory/"approvals.json")
        ledger.approve(request["request_id"],confirmation=request["confirmation"])
        data=json.loads((self.directory/"approvals.json").read_text(encoding="utf-8"))
        del data["requests"][request["request_id"]]["preconditions"]
        (self.directory/"approvals.json").write_text(json.dumps(data),encoding="utf-8")
        runner=Mock(return_value=0)
        result=scheduler.tick(self.directory,runner=runner)
        self.assertFalse(result["ran"])
        self.assertIn("attestation",result["reason"])
        runner.assert_not_called()

    def test_codex_recovery_checkpoint_replay_cannot_reuse_single_use_approval(self):
        import orchestration
        state=autopilot.new_state("c"*40)
        autopilot.transition(state,"RUNNING")
        autopilot.transition(state,"VERIFYING")
        autopilot.save_state(self.directory/"state.json",state)
        request=orchestration.queue_approval(self.directory,task="Task 28",action="codex_service_recovery",
            scope="One safe local repair retry; suite=autopilot self-tests; category=codex_mcp_unavailable; exit=1",
            resume_kind="safe_local_verification")
        ledger=approvals.ApprovalLedger(self.directory/"approvals.json")
        ledger.approve(request["request_id"],confirmation=request["confirmation"])
        runner=Mock(return_value=0)
        # A restored service is explicitly attested by the action-specific confirmation.
        scheduler.tick(self.directory,runner=runner)
        state=autopilot.load_state(self.directory/"state.json")
        state["status"]="WAITING_APPROVAL"
        autopilot.save_state(self.directory/"state.json",state)
        replay=scheduler.tick(self.directory,runner=runner)
        self.assertFalse(replay["ran"])
        self.assertEqual(runner.call_count,1)

    def test_codex_recovery_executor_crash_is_recorded_unknown_and_never_replayed(self):
        state=self.write_state("READY",approval_resume_in_flight={"request_id":"spent","action":"codex_service_recovery"})
        runner=Mock(return_value=0)
        result=scheduler.tick(self.directory,runner=runner)
        self.assertFalse(result["ran"])
        self.assertEqual(result["status"],"WAITING_APPROVAL")
        runner.assert_not_called()
        saved=autopilot.load_state(self.directory/"state.json")
        self.assertIn("unknown",saved["blocking_reason"])
        self.assertEqual(saved["status"],"WAITING_APPROVAL")

    def test_force_killed_autopilot_process_does_not_replay_recovery_effect(self):
        import orchestration
        state=autopilot.new_state("1"*40)
        autopilot.transition(state,"RUNNING")
        autopilot.transition(state,"VERIFYING")
        autopilot.save_state(self.directory/"state.json",state)
        request=orchestration.queue_approval(self.directory,task="Task 28",action="codex_service_recovery",
            scope="One safe local repair retry; suite=autopilot self-tests; category=codex_mcp_unavailable; exit=1",
            resume_kind="safe_local_verification")
        ledger=approvals.ApprovalLedger(self.directory/"approvals.json")
        self.assertTrue(ledger.approve(request["request_id"],confirmation=request["confirmation"]))
        ctx=multiprocessing.get_context("spawn")
        started=ctx.Event()
        child=ctx.Process(target=_run_killable_scheduled_recovery,
                          args=(str(self.directory),started,str(self.directory/"side-effects.log")))
        child.start()
        try:
            self.assertTrue(started.wait(timeout=15),"isolated Autopilot child did not enter the executor")
            child.terminate()
            child.join(timeout=10)
            self.assertFalse(child.is_alive(),"force-terminated Autopilot child did not exit")
            self.assertNotEqual(child.exitcode,0,"child unexpectedly completed without termination")
            consumed=ledger.get(request["request_id"])
            self.assertEqual(consumed["status"],"OUTCOME_UNKNOWN")
            self.assertEqual(consumed["uses"],1)
            effect_path=self.directory/"side-effects.log"
            self.assertEqual(effect_path.read_text(encoding="utf-8").splitlines(),["effect"])
            restarted_runner=Mock(return_value=0)
            resumed=scheduler.tick(self.directory,runner=restarted_runner)
            self.assertFalse(resumed["ran"])
            self.assertEqual(resumed["status"],"WAITING_APPROVAL")
            restarted_runner.assert_not_called()
            saved=autopilot.load_state(self.directory/"state.json")
            self.assertEqual(saved["status"],"WAITING_APPROVAL")
            self.assertIn("unknown",saved["blocking_reason"])
            self.assertEqual(effect_path.read_text(encoding="utf-8").splitlines(),["effect"])
        finally:
            if child.is_alive():
                child.terminate()
                child.join(timeout=10)

    def test_crash_after_reservation_before_checkpoint_is_reported_as_unknown(self):
        import orchestration
        state=autopilot.new_state("f"*40)
        autopilot.transition(state,"RUNNING")
        autopilot.transition(state,"VERIFYING")
        autopilot.save_state(self.directory/"state.json",state)
        request=orchestration.queue_approval(self.directory,task="Task 28",action="codex_service_recovery",
            scope="One safe local repair retry; suite=autopilot self-tests; category=codex_mcp_unavailable; exit=1",
            resume_kind="safe_local_verification")
        ledger=approvals.ApprovalLedger(self.directory/"approvals.json")
        ledger.approve(request["request_id"],confirmation=request["confirmation"])
        ledger.reserve(request["request_id"],task="Task 28",action="codex_service_recovery",
            scope="One safe local repair retry; suite=autopilot self-tests; category=codex_mcp_unavailable; exit=1")
        runner=Mock(return_value=0)
        result=scheduler.tick(self.directory,runner=runner)
        self.assertFalse(result["ran"])
        self.assertIn("unknown",result["reason"])
        runner.assert_not_called()
        saved=autopilot.load_state(self.directory/"state.json")
        self.assertIn("unknown",saved["blocking_reason"])
        self.assertEqual(saved["status"],"WAITING_APPROVAL")

    def test_codex_recovery_runner_exception_leaves_unknown_approval_and_no_replay(self):
        import orchestration
        state=autopilot.new_state("d"*40)
        autopilot.transition(state,"RUNNING")
        autopilot.transition(state,"VERIFYING")
        autopilot.save_state(self.directory/"state.json",state)
        request=orchestration.queue_approval(self.directory,task="Task 28",action="codex_service_recovery",
            scope="One safe local repair retry; suite=autopilot self-tests; category=codex_mcp_unavailable; exit=1",
            resume_kind="safe_local_verification")
        ledger=approvals.ApprovalLedger(self.directory/"approvals.json")
        ledger.approve(request["request_id"],confirmation=request["confirmation"])
        runner=Mock(side_effect=RuntimeError("simulated executor interruption"))
        with self.assertRaisesRegex(RuntimeError,"simulated executor interruption"):
            scheduler.tick(self.directory,runner=runner)
        self.assertEqual(ledger.get(request["request_id"])["status"],"OUTCOME_UNKNOWN")
        recovery=scheduler.tick(self.directory,runner=runner)
        self.assertFalse(recovery["ran"])
        self.assertEqual(recovery["status"],"WAITING_APPROVAL")
        self.assertEqual(runner.call_count,1)

    def test_pause_and_emergency_stop_are_checked_before_resume(self):
        self.write_state("READY")
        (self.directory / "PAUSE").write_text("pause", encoding="utf-8")
        runner = Mock()
        self.assertEqual(scheduler.tick(self.directory, runner=runner)["status"], "PAUSED")
        runner.assert_not_called()
        (self.directory / "PAUSE").unlink()
        (self.directory / "STOP").write_text("stop", encoding="utf-8")
        self.assertEqual(scheduler.tick(self.directory, runner=runner)["status"], "STOPPED")
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
