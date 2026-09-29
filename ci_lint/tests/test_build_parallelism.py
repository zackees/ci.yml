"""GEN-012 -- ci_lint/rules/build_parallelism.py.

Pure text scan, no PyYAML/yq dependency, so unlike ci_lint/tests/test_workflows.py
these tests are never skipped for missing YAML tooling.
"""

from __future__ import annotations

import unittest

from ci_lint.rules.build_parallelism import check_gen_012
from ci_lint.tests.helpers import fixture


def rule_ids(findings) -> list[str]:
    return [f.rule for f in findings]


class BuildParallelismTest(unittest.TestCase):
    def test_red_env_and_run_settings_flagged(self) -> None:
        findings = check_gen_012(fixture("GEN-012", "red"))
        ids = rule_ids(findings)
        # workflow-level(ish job) env SOLDR_JOBS, step env CARGO_BUILD_JOBS,
        # and `cargo build -j 2` on a run: line -- three distinct findings.
        self.assertEqual(ids.count("GEN-012"), 3)
        messages = " ".join(f.message for f in findings)
        self.assertIn("SOLDR_JOBS", messages)
        self.assertIn("CARGO_BUILD_JOBS", messages)
        self.assertIn("-j 2", messages)

    def test_green_unset_comment_mention_and_reasoned_exception(self) -> None:
        findings = check_gen_012(fixture("GEN-012", "green"))
        self.assertNotIn("GEN-012", rule_ids(findings))
