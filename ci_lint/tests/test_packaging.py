"""PKG-003, PKG-004, PKG-005 -- ci_lint/rules/packaging.py. tomllib + AST
only; no YAML tooling needed."""

from __future__ import annotations

import unittest

from ci_lint.rules.packaging import check_pkg_003, check_pkg_004, check_pkg_005
from ci_lint.schema import load_ci_toml
from ci_lint.tests.helpers import fixture


class PackagingFixtureTest(unittest.TestCase):
    def test_pkg_004_maturin_backend(self) -> None:
        self.assertIn("PKG-004", [f.rule for f in check_pkg_004(fixture("PKG-004", "red"))])
        self.assertNotIn("PKG-004", [f.rule for f in check_pkg_004(fixture("PKG-004", "green"))])

    def test_pkg_003_cli_shadowed_by_python_script(self) -> None:
        repo = fixture("PKG-003", "red")
        ci, _ = load_ci_toml(repo)
        self.assertIn("PKG-003", [f.rule for f in check_pkg_003(ci, repo)])

        repo = fixture("PKG-003", "green")
        ci, _ = load_ci_toml(repo)
        self.assertNotIn("PKG-003", [f.rule for f in check_pkg_003(ci, repo)])

    def test_pkg_005_try_except_around_native_import(self) -> None:
        self.assertIn("PKG-005", [f.rule for f in check_pkg_005(fixture("PKG-005", "red"))])
        self.assertNotIn("PKG-005", [f.rule for f in check_pkg_005(fixture("PKG-005", "green"))])


if __name__ == "__main__":
    unittest.main()
