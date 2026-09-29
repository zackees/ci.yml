"""RUST-017 -- ci_lint/rules/rust_nocapture.py (zackees/ci.yml#79, M2-40)."""

from __future__ import annotations

import unittest

from ci_lint.finding import Status
from ci_lint.rules.rust_nocapture import check_rust_017
from ci_lint.tests.helpers import fixture, requires_yaml_tooling


@requires_yaml_tooling
class Rust017Test(unittest.TestCase):
    def test_green_is_clean(self) -> None:
        self.assertEqual([], check_rust_017(fixture("RUST-017", "green")))

    def test_green_same_line_allow_comment(self) -> None:
        self.assertEqual([], check_rust_017(fixture("RUST-017", "green-allow")))

    def test_red_run_lines(self) -> None:
        findings = check_rust_017(fixture("RUST-017", "red"))
        self.assertEqual(["RUST-017", "RUST-017"], [f.rule for f in findings])
        self.assertTrue(all(f.status == Status.VIOLATION for f in findings))
        text = " ".join(f.message for f in findings)
        self.assertIn("--no-capture", text)
        self.assertIn("--test-threads 1", text)

    def test_red_one_level_into_ci_script(self) -> None:
        findings = check_rust_017(fixture("RUST-017", "red-script"))
        self.assertEqual(1, len(findings))
        f = findings[0]
        self.assertEqual(("RUST-017", "ci/test.py", 3, Status.VIOLATION), (f.rule, f.path, f.line, f.status))

    def test_dynamic_append_is_needs_review(self) -> None:
        findings = check_rust_017(fixture("RUST-017", "review-script"))
        self.assertEqual(1, len(findings))
        self.assertEqual(Status.NEEDS_REVIEW, findings[0].status)
        self.assertEqual(6, findings[0].line)


if __name__ == "__main__":
    unittest.main()
