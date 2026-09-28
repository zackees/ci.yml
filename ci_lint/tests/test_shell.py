"""GEN-005 -- ci_lint/rules/shell.py."""

from __future__ import annotations

import unittest

from ci_lint.rules.shell import check_group3
from ci_lint.schema import load_ci_toml
from ci_lint.tests.helpers import fixture, requires_yaml_tooling


@requires_yaml_tooling
class ShellFixtureTest(unittest.TestCase):
    def test_gen_005_multiline_run_step(self) -> None:
        repo = fixture("GEN-005", "red")
        ci, _ = load_ci_toml(repo)
        rules = [f.rule for f in check_group3(ci, repo)]
        self.assertIn("GEN-005", rules)

        repo = fixture("GEN-005", "green")
        ci, _ = load_ci_toml(repo)
        rules = [f.rule for f in check_group3(ci, repo)]
        self.assertNotIn("GEN-005", rules)


if __name__ == "__main__":
    unittest.main()
