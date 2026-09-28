"""The planner (section D of the round-1A brief): untagged PR; [ci-windows];
[ci-windows][ci-test-integration]; [ci-full]; [no-test] (mergeable false);
[release][no-test] (error); push main (no tag reading, cache write);
workflow_dispatch release; unknown [ci-foo]."""

from __future__ import annotations

import unittest

from ci_lint.plan import PlanError, compute_plan
from ci_lint.schema import load_ci_toml
from ci_lint.tests.helpers import fixture

SHA = "c" * 40


def load_ci():
    ci, findings = load_ci_toml(fixture("TAG-001", "green"))
    assert ci is not None, findings
    return ci


class PlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ci = load_ci()

    def test_untagged_pr(self) -> None:
        plan = compute_plan(self.ci, event_name="pull_request", title="fix a bug")
        self.assertEqual("pr", plan.flow)
        self.assertEqual((), plan.tags)
        self.assertEqual({"linux-x64"}, {p.id for p in plan.platforms})
        self.assertEqual({"unit", "smoke"}, set(plan.suites))
        self.assertTrue(plan.mergeable)
        self.assertEqual("read", plan.cache_mode)
        self.assertEqual("none", plan.publish)

    def test_ci_windows_adds_platform_group(self) -> None:
        plan = compute_plan(self.ci, event_name="pull_request", title="[ci-windows] fix")
        self.assertEqual({"linux-x64", "windows-x64"}, {p.id for p in plan.platforms})
        self.assertEqual({"unit", "smoke"}, set(plan.suites))
        self.assertTrue(plan.mergeable)

    def test_ci_windows_and_ci_test_integration(self) -> None:
        plan = compute_plan(
            self.ci, event_name="pull_request", title="[ci-windows][ci-test-integration] fix"
        )
        self.assertEqual({"linux-x64", "windows-x64"}, {p.id for p in plan.platforms})
        self.assertEqual({"unit", "smoke", "integration"}, set(plan.suites))

    def test_ci_full(self) -> None:
        plan = compute_plan(self.ci, event_name="pull_request", title="[ci-full] fix")
        self.assertEqual({"linux-x64", "windows-x64"}, {p.id for p in plan.platforms})
        self.assertEqual({"unit", "smoke", "integration"}, set(plan.suites))

    def test_no_test_removes_all_suites_and_is_not_mergeable(self) -> None:
        plan = compute_plan(self.ci, event_name="pull_request", title="[no-test] wip")
        self.assertEqual(set(), set(plan.suites))
        self.assertFalse(plan.mergeable)

    def test_release_with_no_test_is_an_error(self) -> None:
        with self.assertRaises(PlanError):
            compute_plan(self.ci, event_name="pull_request", title="[release][no-test] wip")

    def test_release_with_no_test_suffix_is_also_an_error(self) -> None:
        with self.assertRaises(PlanError):
            compute_plan(
                self.ci, event_name="pull_request", title="[release][no-test-integration] wip"
            )

    def test_push_main_ignores_tags_and_writes_cache(self) -> None:
        plan = compute_plan(self.ci, event_name="push", title="[ci-windows] should be ignored")
        self.assertEqual("main", plan.flow)
        self.assertEqual((), plan.tags)
        self.assertEqual({"linux-x64"}, {p.id for p in plan.platforms})
        self.assertEqual("write", plan.cache_mode)
        self.assertTrue(plan.mergeable)

    def test_workflow_dispatch_release_requires_sha(self) -> None:
        with self.assertRaises(PlanError):
            compute_plan(self.ci, event_name="workflow_dispatch", title="")

        plan = compute_plan(
            self.ci, event_name="workflow_dispatch", title="", dispatch_sha=SHA
        )
        self.assertEqual("release", plan.flow)
        self.assertEqual(SHA, plan.dispatch_sha)
        self.assertEqual({"linux-x64", "windows-x64"}, {p.id for p in plan.platforms})
        self.assertEqual({"unit", "smoke", "integration", "perf"}, set(plan.suites))
        self.assertEqual("mock", plan.publish)  # flow.release.publish = "pypi" -> publish.pypi.mode

    def test_unknown_reserved_tag_is_ignored_by_the_planner(self) -> None:
        base = compute_plan(self.ci, event_name="pull_request", title="untagged")
        plan = compute_plan(self.ci, event_name="pull_request", title="[ci-foo] weird")
        self.assertEqual(base.platforms, plan.platforms)
        self.assertEqual(base.suites, plan.suites)

    def test_schedule_selects_nightly(self) -> None:
        plan = compute_plan(self.ci, event_name="schedule", title="")
        self.assertEqual("nightly", plan.flow)
        self.assertEqual("write", plan.cache_mode)
        self.assertEqual("none", plan.publish)  # nightly overrides release's publish

    def test_release_tag_switches_flow_and_publish_is_rehearsal(self) -> None:
        plan = compute_plan(self.ci, event_name="pull_request", title="[release] ship it")
        self.assertEqual("release", plan.flow)
        self.assertEqual("rehearsal", plan.publish)

    def test_digest_is_stable_for_the_same_selection(self) -> None:
        p1 = compute_plan(self.ci, event_name="pull_request", title="[ci-windows] x")
        p2 = compute_plan(self.ci, event_name="pull_request", title="[ci-windows] y")
        self.assertEqual(p1.digest, p2.digest)
        p3 = compute_plan(self.ci, event_name="pull_request", title="untagged")
        self.assertNotEqual(p1.digest, p3.digest)


class RequiredJobsAndPlatformLanesTest(unittest.TestCase):
    """Round-2A amendments 1 and 3: the gate job-id convention and the
    needs_platform_lanes definition (true exactly when the plan selects
    any platform other than [flow.pr]'s own default fast-lane platforms,
    not merely "more than one platform")."""

    def setUp(self) -> None:
        self.ci = load_ci()

    def test_untagged_pr_is_the_fast_lane_only(self) -> None:
        plan = compute_plan(self.ci, event_name="pull_request", title="fix a bug")
        self.assertFalse(plan.needs_platform_lanes)
        self.assertEqual(("precheck", "fast", "dylint"), plan.required_jobs)
        self.assertNotIn("ci-ok", plan.required_jobs)
        self.assertFalse(any(j.startswith("build-") for j in plan.required_jobs))

    def test_ci_windows_tag_needs_platform_lanes_and_matrix_jobs(self) -> None:
        plan = compute_plan(self.ci, event_name="pull_request", title="[ci-windows] fix")
        self.assertTrue(plan.needs_platform_lanes)
        self.assertEqual(
            ("precheck", "fast", "dylint", "platform-build", "platform-run"), plan.required_jobs
        )

    def test_release_needs_platform_lanes_even_though_nothing_was_added_by_a_tag(self) -> None:
        # flow.release's own base is platforms = "all" (both linux-x64 and
        # windows-x64 in this fixture) -- no tag added anything, but the
        # selection still goes beyond [flow.pr]'s single-platform default,
        # so the platform-build/platform-run matrix is still required.
        plan = compute_plan(self.ci, event_name="pull_request", title="[release] ship it")
        self.assertEqual({"linux-x64", "windows-x64"}, {p.id for p in plan.platforms})
        self.assertTrue(plan.needs_platform_lanes)
        self.assertIn("platform-build", plan.required_jobs)
        self.assertIn("platform-run", plan.required_jobs)

    def test_nightly_needs_platform_lanes(self) -> None:
        plan = compute_plan(self.ci, event_name="schedule", title="")
        self.assertEqual({"linux-x64", "windows-x64"}, {p.id for p in plan.platforms})
        self.assertTrue(plan.needs_platform_lanes)
        self.assertIn("platform-build", plan.required_jobs)
        self.assertIn("platform-run", plan.required_jobs)

    def test_push_main_matches_the_pr_default_fast_lane(self) -> None:
        # flow.main extends pr, so it inherits the same single-platform
        # base -- no platform lanes needed on a plain main push.
        plan = compute_plan(self.ci, event_name="push", title="")
        self.assertEqual({"linux-x64"}, {p.id for p in plan.platforms})
        self.assertFalse(plan.needs_platform_lanes)
        self.assertEqual(("precheck", "fast", "dylint"), plan.required_jobs)


if __name__ == "__main__":
    unittest.main()
