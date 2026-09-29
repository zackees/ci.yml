"""RUST-015 -- ci_lint/rules/rust_test_selectors.py (zackees/ci.yml#81, M2-40)."""

from __future__ import annotations

import unittest

from ci_lint.finding import Status
from ci_lint.rules.rust_test_selectors import check_rust_015
from ci_lint.tests.helpers import fixture, requires_yaml_tooling


@requires_yaml_tooling
class Rust015Test(unittest.TestCase):
    def test_green_is_clean(self) -> None:
        self.assertEqual([], check_rust_015(fixture("RUST-015", "green")))

    def test_green_same_line_allow_comment(self) -> None:
        self.assertEqual([], check_rust_015(fixture("RUST-015", "green-allow")))

    def test_red_run_lines(self) -> None:
        findings = check_rust_015(fixture("RUST-015", "red"))
        self.assertEqual(["RUST-015", "RUST-015"], [f.rule for f in findings])
        self.assertTrue(all(f.status == Status.VIOLATION for f in findings))
        self.assertIn("-E", findings[0].message)
        self.assertIn("name filter 'daemon_'", findings[1].message)

    def test_red_script_tests_flag_is_not_a_selector(self) -> None:
        findings = check_rust_015(fixture("RUST-015", "red-script"))
        self.assertEqual(1, len(findings))
        self.assertEqual(("ci/test.py", 3), (findings[0].path, findings[0].line))


if __name__ == "__main__":
    unittest.main()
