import tempfile
import unittest
from pathlib import Path
import task_plan


PLAN=Path(__file__).parents[2]/"docs/superpowers/plans/2026-10-17-shuliu-agent-foundation-v0.1-implementation.md"


class PlanTests(unittest.TestCase):
    def test_reads_original_task_names_dependencies_and_release_gates(self):
        catalog=task_plan.load_catalog(PLAN)
        self.assertEqual(catalog.task_order,(28,29,30,31,32,33,34,35,36,27))
        self.assertIn(6,catalog.tasks[28].dependencies)
        self.assertIn(23,catalog.tasks[28].dependencies)
        self.assertIn("real_model_provider_connection",catalog.tasks[28].release_gates)
        self.assertIn(36,catalog.tasks[27].dependencies)
        self.assertIn(28,catalog.tasks[27].dependencies)
        self.assertTrue(catalog.source_sha256)

    def test_task29_cannot_start_until_task28_is_passed(self):
        catalog=task_plan.load_catalog(PLAN)
        self.assertEqual(catalog.next_task({28:"BLOCKED"}),28)
        self.assertEqual(catalog.next_task({28:"PASSED"}),29)

    def test_task27_is_last_and_requires_all_supplemental_tasks(self):
        catalog=task_plan.load_catalog(PLAN)
        statuses={number:"PASSED" for number in range(28,37)}
        self.assertEqual(catalog.next_task(statuses),27)
        statuses[27]="PASSED"
        self.assertIsNone(catalog.next_task(statuses))
        statuses[35]="BLOCKED"
        self.assertEqual(catalog.next_task(statuses),35)

    def test_changed_or_incomplete_plan_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"plan.md"
            p.write_text("### Task 28 — missing body\n",encoding="utf-8")
            with self.assertRaises(task_plan.PlanError):
                task_plan.load_catalog(p)


if __name__=="__main__":
    unittest.main()
