"""REL-001, REL-002 -- ci_lint/rules/release_gate.py (zackees/ci.yml#8)."""

from __future__ import annotations

import unittest

from ci_lint.rules.release_gate import check_rel_001, check_rel_002
from ci_lint.tests.helpers import fixture


class ReleaseGateFixtureTest(unittest.TestCase):
    def test_rel_001_gh_json_without_env_strip(self) -> None:
        rules = [f.rule for f in check_rel_001(fixture("REL-001", "red"))]
        self.assertIn("REL-001", rules)

    def test_rel_001_gh_json_with_env_strip_is_clean(self) -> None:
        rules = [f.rule for f in check_rel_001(fixture("REL-001", "green"))]
        self.assertNotIn("REL-001", rules)

    def test_rel_002_soldr_side_files_not_ignored(self) -> None:
        rules = [f.rule for f in check_rel_002(fixture("REL-002", "red"))]
        self.assertIn("REL-002", rules)

    def test_rel_002_soldr_side_files_ignored(self) -> None:
        rules = [f.rule for f in check_rel_002(fixture("REL-002", "green"))]
        self.assertNotIn("REL-002", rules)


if __name__ == "__main__":
    unittest.main()
