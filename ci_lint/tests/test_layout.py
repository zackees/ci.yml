"""LAYOUT-001 -- ci_lint/rules/layout.py. Pure filesystem + regex; no YAML
tooling needed."""

from __future__ import annotations

import unittest

from ci_lint.rules.layout import check_group6
from ci_lint.schema import load_ci_toml
from ci_lint.tests.helpers import fixture


class LayoutFixtureTest(unittest.TestCase):
    def test_layout_001_host_selector_outside_facade(self) -> None:
        repo = fixture("LAYOUT-001", "red")
        ci, _ = load_ci_toml(repo)
        self.assertIn("LAYOUT-001", [f.rule for f in check_group6(ci, repo)])

        repo = fixture("LAYOUT-001", "green")
        ci, _ = load_ci_toml(repo)
        self.assertNotIn("LAYOUT-001", [f.rule for f in check_group6(ci, repo)])


if __name__ == "__main__":
    unittest.main()
