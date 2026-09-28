"""`ci-lint gate` -- ci_lint/runtime/gate.py.

Recorded plan.json/needs.json inputs under
ci_lint/tests/fixtures/runtime/gate/ (round-2A brief, part 2d).
"""

from __future__ import annotations

import json
import unittest

from ci_lint.runtime.gate import compute_gate, render_text
from ci_lint.tests.helpers import FIXTURES

GATE_FIXTURES = FIXTURES / "runtime" / "gate"


def _load(name: str) -> dict[str, object]:
    return json.loads((GATE_FIXTURES / name).read_text(encoding="utf-8"))


class GateTest(unittest.TestCase):
    def test_all_required_jobs_succeed_is_ok(self) -> None:
        plan = _load("plan.json")
        needs = _load("needs-success.json")
        report = compute_gate(plan, needs)
        self.assertTrue(report.ok)
        self.assertEqual([], [f for f in report.findings])

    def test_skipped_required_job_is_test_001_and_not_ok(self) -> None:
        plan = _load("plan.json")
        needs = _load("needs-skipped.json")
        report = compute_gate(plan, needs)
        self.assertFalse(report.ok)
        rules = [f.rule for f in report.findings]
        self.assertIn("TEST-001", rules)
        skipped = next(s for s in report.statuses if s.job_id == "dylint")
        self.assertEqual("skipped", skipped.result)
        self.assertFalse(skipped.ok)

    def test_missing_job_is_needs_review_not_a_silent_pass(self) -> None:
        plan = _load("plan.json")
        needs = _load("needs-missing-job.json")
        report = compute_gate(plan, needs)
        self.assertFalse(report.ok)
        dylint_finding = next(f for f in report.findings if f.path == "dylint")
        self.assertEqual("needs_review", dylint_finding.status.value)

    def test_not_mergeable_plan_fails_even_with_successful_jobs(self) -> None:
        plan = _load("plan-not-mergeable.json")
        needs = _load("needs-success.json")
        report = compute_gate(plan, needs)
        self.assertFalse(report.ok)
        self.assertFalse(report.mergeable)
        assert report.not_mergeable_message is not None
        self.assertIn("not mergeable:", report.not_mergeable_message)
        self.assertIn("no-test", report.not_mergeable_message)

    def test_render_text_does_not_crash(self) -> None:
        plan = _load("plan.json")
        needs = _load("needs-success.json")
        report = compute_gate(plan, needs)
        text = render_text(report)
        self.assertIn("ci-lint gate: OK", text)

    def test_platform_lanes_plan_requires_the_matrix_job_ids(self) -> None:
        """Round-2A amendment 1: when needs_platform_lanes is true, the
        required jobs are exactly precheck, fast, dylint, platform-build,
        platform-run -- 'ci-ok' (the gate itself) is never included."""

        plan = _load("plan-platform-lanes.json")
        self.assertEqual(
            ["precheck", "fast", "dylint", "platform-build", "platform-run"], plan["required_jobs"]
        )
        needs = _load("needs-platform-lanes-success.json")
        report = compute_gate(plan, needs)
        self.assertTrue(report.ok, msg=render_text(report))
        self.assertEqual(
            ["precheck", "fast", "dylint", "platform-build", "platform-run"],
            [s.job_id for s in report.statuses],
        )
        self.assertNotIn("ci-ok", report.required_jobs)


if __name__ == "__main__":
    unittest.main()
