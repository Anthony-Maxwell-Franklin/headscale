"""Guard scientific claims against build failures and unrelated test failures."""
import unittest

from verify_repair_validation import check_test_events


class RepairEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.case = dict(issue=2121, regression_tests=["regression"], control_tests=["control"])
        self.events = [
            dict(Action="run", Test="control"), dict(Action="pass", Test="control"),
            dict(Action="run", Test="regression"),
            dict(Action="output", Test="regression", Output=(
                "duplicate-name rename was accepted\n"
                "duplicate given name persisted: laptop\n"
                'node 2 name = "laptop", want "server"\n')),
            dict(Action="fail", Test="regression"),
        ]

    def test_actual_semantic_failure_and_normal_control_are_required(self):
        outcome = check_test_events(self.events, self.case, "parent", 1)
        self.assertEqual(outcome, {"regression": "fail", "control": "pass"})
        fixed = [dict(Action="run", Test=name) for name in ("regression", "control")]
        fixed += [dict(Action="pass", Test=name) for name in ("regression", "control")]
        self.assertEqual(check_test_events(fixed, self.case, "fixed", 0),
                         {"regression": "pass", "control": "pass"})

    def test_build_failure_without_executed_targets_is_inconclusive(self):
        events = [dict(Action="output", Output="cannot compile package"),
                  dict(Action="fail", Package="example")]
        self.assertRaises(ValueError, check_test_events, events, self.case, "parent", 1)
        absent_run = [e for e in self.events if e.get("Action") != "run"]
        self.assertRaises(ValueError, check_test_events, absent_run, self.case, "parent", 1)

    def test_unrelated_failure_cannot_establish_the_reported_defect(self):
        events = [dict(e) for e in self.events]
        events[3]["Output"] = "database setup failed"
        self.assertRaises(ValueError, check_test_events, events, self.case, "parent", 1)
        events = [dict(e) for e in self.events]
        events[1]["Action"] = "skip"
        self.assertRaises(ValueError, check_test_events, events, self.case, "parent", 1)


if __name__ == "__main__":
    unittest.main()
