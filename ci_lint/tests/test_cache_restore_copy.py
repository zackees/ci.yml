"""GEN-013 -- ci_lint/rules/cache_restore_copy.py.

Pure text scan, no PyYAML/yq dependency, so unlike ci_lint/tests/test_workflows.py
these tests are never skipped for missing YAML tooling.
"""

from __future__ import annotations

import unittest

from ci_lint.rules.cache_restore_copy import check_gen_013
from ci_lint.tests.helpers import fixture


def rule_ids(findings) -> list[str]:
    return [f.rule for f in findings]


class CacheRestoreCopyTest(unittest.TestCase):
    def test_red_workflow_step_copies_over_restored_dylint_tree(self) -> None:
        findings = check_gen_013(fixture("GEN-013", "red"))
        ids = rule_ids(findings)
        # one from the workflow's `cp -r` after actions/cache, one from the
        # delegated ci/copy_dylint.py's shutil.copytree onto target/dylint.
        self.assertEqual(ids.count("GEN-013"), 2)
        messages = " ".join(f.message for f in findings)
        self.assertIn("read-only", messages)
        paths = {f.path for f in findings}
        self.assertIn(".github/workflows/ci.yml", paths)
        self.assertIn("ci/copy_dylint.py", paths)

    def test_green_no_copy_after_restore_and_reasoned_exception(self) -> None:
        findings = check_gen_013(fixture("GEN-013", "green"))
        self.assertNotIn("GEN-013", rule_ids(findings))
