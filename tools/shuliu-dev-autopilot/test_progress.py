import json
import tempfile
import unittest
from pathlib import Path
import progress
import task_plan


PLAN=Path(__file__).parents[2]/"docs/superpowers/plans/2026-10-17-shuliu-agent-foundation-v0.1-implementation.md"


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.catalog=task_plan.load_catalog(PLAN)
        self.data=progress.initial_progress(self.catalog,last_commit_sha="a"*40)
        progress.record_commit(self.data,28,"b"*40)

    def test_current_task28_is_blocked_and_future_tasks_are_not_started(self):
        self.assertEqual(progress.next_task(self.data),28)
        self.assertEqual(self.data["tasks"]["28"]["status"],"BLOCKED")
        self.assertEqual(self.data["tasks"]["29"]["status"],"NOT_STARTED")


    def test_current_status_sync_is_durable_and_does_not_release_task29(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"project-progress.json"
            progress.ensure_progress(path,self.catalog,last_commit_sha="a"*40)
            progress.record_current_status(path,self.catalog,28,"RUNNING",last_commit_sha="a"*40)
            progress.record_current_status(path,self.catalog,28,"BLOCKED",last_commit_sha="a"*40,
                                           blocking_reason="real provider and Windows CI evidence missing")
            saved=progress.load_progress(path,self.catalog)
            self.assertEqual(saved["tasks"]["28"]["status"],"BLOCKED")
            self.assertEqual(progress.next_task(saved),28)

    def test_task29_is_not_released_before_task28_gates_pass(self):
        with self.assertRaises(progress.ProgressError):
            progress.set_status(self.data,self.catalog,29,"RUNNING")

    def test_task28_cannot_pass_without_each_original_gate_and_bound_sha(self):
        with self.assertRaises(progress.ProgressError):
            progress.set_status(self.data,self.catalog,28,"PASSED",gate_evidence={})
        evidence={
          "real_model_provider_connection":{"type":"provider","source":"desktop_secretref_gateway_canary",
            "status":"success","one_time_request":True,"sha":"b"*40,
            "request_id":"one-time-id","trace_id":"trace-safe",
            "verified_at":"2026-10-22T00:00:00Z","expires_at":"2026-10-28T00:00:00Z"},
          "model_provider_license_security":{"type":"manual_review","source":"human_provider_review",
            "status":"approved","sha":"b"*40,"review_ref":"docs/evidence/provider-review.md",
            "reviewer_confirmed":True,"license_reviewed":True,"data_processing_reviewed":True,
            "security_reviewed":True,"verified_at":"2026-10-22T00:00:00Z",
            "expires_at":"2027-01-20T00:00:00Z"},
        }
        evidence["windows_validation"]={"type":"github","conclusion":"success","target_sha":"b"*40,
            "sha":"b"*40,"run_url":"https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/123",
            "run_id":"123","repository":"paisleyli0116-boop/shuliu-agent","workflow":"Windows Validation",
            "source":"github_actions_api","verified_at":"2026-10-22T00:00:00Z",
            "expires_at":"2026-11-21T00:00:00Z"}
        progress.set_status(self.data,self.catalog,28,"PASSED",gate_evidence=evidence)
        self.assertEqual(progress.next_task(self.data),29)

    def test_progress_roundtrips_and_rejects_incorrect_ci_sha(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"progress.json"
            progress.save_progress(path,self.data)
            loaded=progress.load_progress(path,self.catalog)
            self.assertEqual(loaded,self.data)
            evidence={
              "real_model_provider_connection":{"type":"provider","source":"desktop_secretref_gateway_canary",
                "status":"success","one_time_request":True,"sha":"b"*40,
                "request_id":"one-time-id","trace_id":"trace-safe",
                "verified_at":"2026-10-22T00:00:00Z","expires_at":"2026-10-28T00:00:00Z"},
              "model_provider_license_security":{"type":"manual_review","source":"human_provider_review",
                "status":"approved","sha":"b"*40,"review_ref":"docs/evidence/provider-review.md",
                "reviewer_confirmed":True,"license_reviewed":True,"data_processing_reviewed":True,
                "security_reviewed":True,"verified_at":"2026-10-22T00:00:00Z",
                "expires_at":"2027-01-20T00:00:00Z"},
            }
            progress.record_commit(loaded,28,"b"*40)
            evidence["windows_validation"]={"type":"github","conclusion":"success","target_sha":"b"*40,
                "sha":"b"*40,"run_url":"https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/123",
                "run_id":"123","repository":"paisleyli0116-boop/shuliu-agent","workflow":"Windows Validation",
                "source":"github_actions_api","verified_at":"2026-10-22T00:00:00Z",
                "expires_at":"2026-11-21T00:00:00Z"}
            progress.set_status(loaded,self.catalog,28,"PASSED",gate_evidence=evidence)
            with self.assertRaises(progress.ProgressError):
                progress.set_gate_evidence(loaded,self.catalog,28,"windows_validation",{
                    "type":"github","conclusion":"success","target_sha":"c"*40,"sha":"b"*40,
                    "run_url":"https://github.com/org/repo/actions/runs/123"
                })

    def test_task28_fake_local_test_cannot_satisfy_provider_gate_or_release_task29(self):
        sha="b"*40
        wrong={"type":"local_test","source":"local_runner","sha":sha,
               "command":"pytest -q","exit_code":0,"recorded_at":"2026-10-22T00:00:00Z",
               "expires_at":"2026-10-29T00:00:00Z"}
        with self.assertRaises(progress.ProgressError):
            progress.set_gate_evidence(self.data,self.catalog,28,"real_model_provider_connection",wrong)
        self.assertEqual(self.data["tasks"]["28"]["status"],"BLOCKED")
        self.assertEqual(progress.next_task(self.data),28)

    def test_task28_rejects_github_evidence_from_wrong_repo_workflow_run_or_expiry(self):
        base={"type":"github","source":"github_actions_api","sha":"b"*40,"target_sha":"b"*40,
              "repository":"paisleyli0116-boop/shuliu-agent","workflow":"Windows Validation",
              "run_id":"123","run_url":"https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/123",
              "conclusion":"success","verified_at":"2026-10-22T00:00:00Z",
              "expires_at":"2026-11-21T00:00:00Z"}
        for field,value in (("repository","attacker/fork"),("workflow","Generic CI"),
                            ("run_id","999"),("expires_at","2026-10-21T00:00:00Z")):
            evidence={**base,field:value}
            with self.subTest(field=field),self.assertRaises(progress.ProgressError):
                progress.set_gate_evidence(self.data,self.catalog,28,"windows_validation",evidence)
            self.assertEqual(progress.next_task(self.data),28)

    def test_manual_review_evidence_rejects_windows_drive_unc_and_device_paths(self):
        base={"type":"manual_review","source":"human_provider_review","status":"approved",
              "sha":"b"*40,"reviewer_confirmed":True,"license_reviewed":True,
              "data_processing_reviewed":True,"security_reviewed":True,
              "verified_at":"2026-10-22T00:00:00Z","expires_at":"2027-01-20T00:00:00Z"}
        invalid_refs=(r"C:\Users\Administrator\review.md", "C:/Users/Administrator/review.md",
                      r"\\server\share\review.md", r"\\?\C:\Users\review.md",
                      r"\\.\PhysicalDrive0", "docs/evidence/../private.md")
        for reference in invalid_refs:
            with self.subTest(reference=reference),self.assertRaises(progress.ProgressError):
                progress.set_gate_evidence(self.data,self.catalog,28,"model_provider_license_security",
                    {**base,"review_ref":reference})
            self.assertEqual(progress.next_task(self.data),28)

    def test_expired_evidence_downgrades_passed_task_to_blocked_on_reload(self):
        evidence={
          "real_model_provider_connection":{"type":"provider","source":"desktop_secretref_gateway_canary",
            "status":"success","one_time_request":True,"sha":"b"*40,"request_id":"one-time-id",
            "trace_id":"trace-safe","verified_at":"2026-10-22T00:00:00Z",
            "expires_at":"2026-10-28T00:00:00Z"},
          "model_provider_license_security":{"type":"manual_review","source":"human_provider_review",
            "status":"approved","sha":"b"*40,"review_ref":"docs/evidence/provider-review.md",
            "reviewer_confirmed":True,"license_reviewed":True,"data_processing_reviewed":True,
            "security_reviewed":True,"verified_at":"2026-10-22T00:00:00Z",
            "expires_at":"2027-01-20T00:00:00Z"},
          "windows_validation":{"type":"github","source":"github_actions_api","conclusion":"success",
            "target_sha":"b"*40,"sha":"b"*40,
            "run_url":"https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/123",
            "run_id":"123","repository":"paisleyli0116-boop/shuliu-agent",
            "workflow":"Windows Validation","verified_at":"2026-10-22T00:00:00Z",
            "expires_at":"2026-11-21T00:00:00Z"},
        }
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"progress.json"
            progress.set_status(self.data,self.catalog,28,"PASSED",gate_evidence=evidence)
            progress.save_progress(path,self.data)
            saved=json.loads(path.read_text(encoding="utf-8"))
            saved["tasks"]["28"]["gate_evidence"]["real_model_provider_connection"]["expires_at"]="2026-10-21T00:00:00Z"
            path.write_text(json.dumps(saved),encoding="utf-8")
            loaded=progress.load_progress(path,self.catalog)
        self.assertEqual(loaded["tasks"]["28"]["status"],"BLOCKED")
        self.assertEqual(progress.next_task(loaded),28)


if __name__=="__main__":
    unittest.main()
