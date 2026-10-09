"""CPP-001 -- ci_lint/rules/cpp_ctest.py (zackees/ci.yml#393): a CI `ctest`
run with no parallelism runs serially (the C/C++ analogue of RUST-017)."""

from __future__ import annotations

import unittest

from ci_lint.finding import Status
from ci_lint.rules.cpp_ctest import check_cpp_001, parse_ctest_args
from ci_lint.tests.helpers import fixture, requires_yaml_tooling


class ParseTest(unittest.TestCase):
    def test_flag_spellings(self) -> None:
        self.assertEqual(0, parse_ctest_args(("-j",)).level)
        self.assertEqual(0, parse_ctest_args(("-j", "--output-on-failure")).level)
        self.assertEqual(8, parse_ctest_args(("-j8",)).level)
        self.assertEqual(8, parse_ctest_args(("-j", "8")).level)
        self.assertEqual(4, parse_ctest_args(("--parallel", "4")).level)
        self.assertEqual(4, parse_ctest_args(("--parallel=4",)).level)
        self.assertIsNone(parse_ctest_args(("--test-dir", "build")).level)
        self.assertEqual("ci", parse_ctest_args(("--preset", "ci")).preset)
        self.assertTrue(parse_ctest_args(("-N",)).list_only)


@requires_yaml_tooling
class Cpp001Test(unittest.TestCase):
    def test_green_is_clean(self) -> None:
        # -j / --parallel N / -jN / inline, job and script CTEST_PARALLEL_LEVEL /
        # -N / a test preset inheriting execution.jobs / a same-line allow.
        self.assertEqual([], check_cpp_001(fixture("CPP-001", "green")))

    def test_red_run_lines(self) -> None:
        findings = check_cpp_001(fixture("CPP-001", "red"))
        self.assertEqual(["CPP-001"] * 5, [f.rule for f in findings])
        self.assertTrue(all(f.status == Status.VIOLATION for f in findings))
        text = " ".join(f.message for f in findings)
        self.assertIn("no -j/--parallel", text)
        self.assertIn("pins the parallel level to 1", text)
        self.assertIn("CTEST_PARALLEL_LEVEL=1", text)

    def test_red_one_level_into_ci_script(self) -> None:
        findings = check_cpp_001(fixture("CPP-001", "red-script"))
        self.assertEqual(1, len(findings))
        f = findings[0]
        self.assertEqual(("CPP-001", "ci/test.py", 3, Status.VIOLATION), (f.rule, f.path, f.line, f.status))

    def test_dynamic_script_and_unknown_preset_are_needs_review(self) -> None:
        findings = check_cpp_001(fixture("CPP-001", "review-script"))
        self.assertEqual([Status.NEEDS_REVIEW] * 2, [f.status for f in findings])
        self.assertEqual({".github/workflows/ci.yml", "ci/test.py"}, {f.path for f in findings})

    def test_local_gate_scripts_are_scanned(self) -> None:
        # `local-gate lint` passes the gate's own ci/*.py scripts, which no
        # workflow run: line may name.
        repo = fixture("CPP-001", "red-script")
        self.assertEqual(1, len(check_cpp_001(repo, extra_scripts=("ci/test.py",))))


if __name__ == "__main__":
    unittest.main()
