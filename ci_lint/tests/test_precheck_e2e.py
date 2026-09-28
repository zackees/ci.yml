"""Section F self-check: one GREEN fixture repo that passes every group.

`_e2e/green/` is a tiny but complete rust-pypi-app repo: it satisfies every
rule in groups 1-9, including the draft ci.toml's own `[[exceptions]]`
pattern (a tracked `ci.sh` that GEN-005 would otherwise flag, approved via
a non-expired exception).
"""

from __future__ import annotations

import unittest

from ci_lint.finding import Status
from ci_lint.precheck import has_violations, render_text, run_precheck
from ci_lint.tests.helpers import fixture, requires_yaml_tooling


class EndToEndGreenFixtureTest(unittest.TestCase):
    def test_precheck_is_fast_on_a_small_repo(self) -> None:
        result = run_precheck(fixture("_e2e", "green"), title="[ci-windows] x")
        self.assertLess(result.elapsed_seconds, 5.0)

    @requires_yaml_tooling
    def test_e2e_green_fixture_has_no_violations(self) -> None:
        result = run_precheck(fixture("_e2e", "green"), title="[ci-windows] x")
        violations = [f for f in result.findings if f.status == Status.VIOLATION]
        self.assertEqual([], violations, msg=render_text(result))
        self.assertFalse(has_violations(result))

    @requires_yaml_tooling
    def test_e2e_green_fixture_reports_the_ci_sh_exception(self) -> None:
        result = run_precheck(fixture("_e2e", "green"), title="[ci-windows] x")
        exceptions = [f for f in result.findings if f.status == Status.APPROVED_EXCEPTION]
        self.assertTrue(any(f.rule == "GEN-005" and f.path == "ci.sh" for f in exceptions))

    @requires_yaml_tooling
    def test_e2e_green_fixture_prints_cache_arithmetic(self) -> None:
        result = run_precheck(fixture("_e2e", "green"), title="[ci-windows] x")
        self.assertIsNotNone(result.cache_arithmetic)
        assert result.cache_arithmetic is not None
        self.assertIn("worst case", result.cache_arithmetic)


if __name__ == "__main__":
    unittest.main()
