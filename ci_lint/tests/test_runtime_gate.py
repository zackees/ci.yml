"""`ci-lint gate` -- ci_lint/runtime/gate.py.

Recorded plan.json/needs.json inputs under
ci_lint/tests/fixtures/runtime/gate/ (round-2A brief, part 2d).
"""

from __future__ import annotations

import json
import unittest

from ci_lint.github_api import FetchFn, GitHubApiError, JsonValue
from ci_lint.runtime.gate import compute_gate, render_text
from ci_lint.tests.helpers import FIXTURES

GATE_FIXTURES = FIXTURES / "runtime" / "gate"

HEAD_SHA = "deadbeef00000000000000000000000000000000"


def _load(name: str) -> dict[str, object]:
    return json.loads((GATE_FIXTURES / name).read_text(encoding="utf-8"))


def _fake_fetch(mapping: dict[str, JsonValue]) -> FetchFn:
    def fetch(url: str, token: str) -> JsonValue:
        assert token
        for needle, payload in mapping.items():
            if needle in url:
                return payload
        raise GitHubApiError(f"unexpected fetch (no fixture stubbed): {url}")

    return fetch


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


class ReuseVerificationTest(unittest.TestCase):
    """Round-3A brief, Part 3: a skipped required job is SUCCESS iff the
    plan marks it reused AND (when a token is available) live
    verification confirms `success` + matching digest + matching head
    SHA. Without a token, a plan-marked reuse is `needs_review` (still a
    non-zero exit), never a silent pass."""

    def test_no_reuse_map_at_all_keeps_skip_as_a_failure(self) -> None:
        plan = _load("plan-reused.json")
        needs = _load("needs-fast-skipped.json")
        report = compute_gate(plan, needs)  # no reuse= kwarg: identical to pre-round-3A behavior
        self.assertFalse(report.ok)
        fast = next(s for s in report.statuses if s.job_id == "fast")
        self.assertFalse(fast.ok)
        self.assertIsNone(fast.reused_from_run)
        self.assertIn("TEST-001", [f.rule for f in report.findings])

    def test_plan_marks_reused_but_no_token_is_needs_review(self) -> None:
        plan = _load("plan-reused.json")
        needs = _load("needs-fast-skipped.json")
        reuse = _load("reuse-fast-only.json")
        report = compute_gate(plan, needs, reuse=reuse, head_sha=HEAD_SHA)  # no fetch/token: local
        self.assertFalse(report.ok)  # needs_review still exits non-zero
        fast = next(s for s in report.statuses if s.job_id == "fast")
        self.assertFalse(fast.ok)
        self.assertEqual(500, fast.reused_from_run)
        finding = next(f for f in report.findings if f.path == "fast")
        self.assertEqual("needs_review", finding.status.value)

    def test_plan_marks_reused_and_live_verification_succeeds(self) -> None:
        plan = _load("plan-reused.json")
        needs = _load("needs-fast-skipped.json")
        reuse = _load("reuse-fast-only.json")
        fetch = _fake_fetch({"actions/jobs/9002": _load("job-9002-success.json")})
        report = compute_gate(
            plan, needs, reuse=reuse, head_sha=HEAD_SHA, fetch=fetch, token="t", repo="zackees/x"
        )
        self.assertTrue(report.ok, msg=render_text(report))
        fast = next(s for s in report.statuses if s.job_id == "fast")
        self.assertTrue(fast.ok)
        self.assertEqual(500, fast.reused_from_run)
        self.assertIn("reused from run 500", render_text(report))

    def test_live_verification_fails_on_digest_mismatch(self) -> None:
        plan = _load("plan-reused.json")
        needs = _load("needs-fast-skipped.json")
        reuse = _load("reuse-fast-only.json")
        fetch = _fake_fetch({"actions/jobs/9002": _load("job-9002-wrong-digest.json")})
        report = compute_gate(
            plan, needs, reuse=reuse, head_sha=HEAD_SHA, fetch=fetch, token="t", repo="zackees/x"
        )
        self.assertFalse(report.ok)
        fast = next(s for s in report.statuses if s.job_id == "fast")
        self.assertFalse(fast.ok)

    def test_live_verification_fails_on_head_sha_mismatch(self) -> None:
        plan = _load("plan-reused.json")
        needs = _load("needs-fast-skipped.json")
        reuse = _load("reuse-fast-only.json")
        fetch = _fake_fetch({"actions/jobs/9002": _load("job-9002-wrong-sha.json")})
        report = compute_gate(
            plan, needs, reuse=reuse, head_sha=HEAD_SHA, fetch=fetch, token="t", repo="zackees/x"
        )
        self.assertFalse(report.ok)

    def test_api_error_during_live_verification_is_needs_review_not_a_pass(self) -> None:
        plan = _load("plan-reused.json")
        needs = _load("needs-fast-skipped.json")
        reuse = _load("reuse-fast-only.json")

        def fetch(url: str, token: str) -> JsonValue:
            raise GitHubApiError("api down")

        report = compute_gate(
            plan, needs, reuse=reuse, head_sha=HEAD_SHA, fetch=fetch, token="t", repo="zackees/x"
        )
        self.assertFalse(report.ok)
        fast = next(s for s in report.statuses if s.job_id == "fast")
        finding = next(f for f in report.findings if f.path == "fast")
        self.assertEqual("needs_review", finding.status.value)
        self.assertEqual(500, fast.reused_from_run)

    def test_platform_build_and_run_require_every_platform_lane_reused(self) -> None:
        plan = _load("plan-reused-platform-lanes.json")
        needs = _load("needs-platform-lanes-skipped.json")
        reuse = _load("reuse-platform-lanes.json")
        fetch = _fake_fetch({"actions/jobs/9004": _load("job-9004-success.json")})
        report = compute_gate(
            plan, needs, reuse=reuse, head_sha=HEAD_SHA, fetch=fetch, token="t", repo="zackees/x"
        )
        self.assertTrue(report.ok, msg=render_text(report))
        for job_id in ("platform-build", "platform-run"):
            status = next(s for s in report.statuses if s.job_id == job_id)
            self.assertTrue(status.ok)
            self.assertEqual(500, status.reused_from_run)

    def test_platform_build_not_reused_when_only_some_lanes_are(self) -> None:
        # plan-reused-platform-lanes.json only has ONE platform lane
        # (windows-x64), so reuse-platform-lanes.json fully covers it --
        # use a plan with a lane the reuse map doesn't cover to prove the
        # "every lane" requirement.
        plan = json.loads(json.dumps(_load("plan-reused-platform-lanes.json")))
        assert isinstance(plan["lane_digests"], dict)
        plan["lane_digests"]["platform:macos-arm64"] = "eeeeeeeeeeee"
        needs = _load("needs-platform-lanes-skipped.json")
        reuse = _load("reuse-platform-lanes.json")  # covers windows-x64 only, not macos-arm64
        report = compute_gate(plan, needs, reuse=reuse, head_sha=HEAD_SHA)
        self.assertFalse(report.ok)
        status = next(s for s in report.statuses if s.job_id == "platform-build")
        self.assertFalse(status.ok)


if __name__ == "__main__":
    unittest.main()
