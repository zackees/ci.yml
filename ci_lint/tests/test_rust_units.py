"""RUST-005, RUST-011, RUST-012 -- ci_lint/cargo_scan.py + rules/rust_units.py.

No cargo is invoked; targets are derived from tomllib + the filesystem.
"""

from __future__ import annotations

import unittest

from ci_lint.cargo_scan import discover_workspace
from ci_lint.rules.rust_units import check_rust_005, check_rust_011, check_rust_012
from ci_lint.schema import load_ci_toml
from ci_lint.tests.helpers import fixture


class RustUnitsFixtureTest(unittest.TestCase):
    def test_rust_012_zero_test_lib_harness(self) -> None:
        repo = fixture("RUST-012", "red")
        ci, _ = load_ci_toml(repo)
        crates = discover_workspace(repo)
        self.assertEqual(1, len(crates), "fixture must resolve exactly one crate")
        self.assertIn("RUST-012", [f.rule for f in check_rust_012(ci, crates, repo)])

        repo = fixture("RUST-012", "green")
        ci, _ = load_ci_toml(repo)
        crates = discover_workspace(repo)
        self.assertNotIn("RUST-012", [f.rule for f in check_rust_012(ci, crates, repo)])

    def test_rust_005_integration_test_overrun(self) -> None:
        repo = fixture("RUST-005", "red")
        ci, _ = load_ci_toml(repo)
        crates = discover_workspace(repo)
        self.assertIn("RUST-005", [f.rule for f in check_rust_005(ci, crates)])

        repo = fixture("RUST-005", "green")
        ci, _ = load_ci_toml(repo)
        crates = discover_workspace(repo)
        self.assertNotIn("RUST-005", [f.rule for f in check_rust_005(ci, crates)])

    def test_rust_011_private_crate_without_publish_false(self) -> None:
        repo = fixture("RUST-011", "red")
        ci, _ = load_ci_toml(repo)
        crates = discover_workspace(repo)
        self.assertIn("RUST-011", [f.rule for f in check_rust_011(ci, crates, repo)])

        repo = fixture("RUST-011", "green")
        ci, _ = load_ci_toml(repo)
        crates = discover_workspace(repo)
        self.assertNotIn("RUST-011", [f.rule for f in check_rust_011(ci, crates, repo)])

    def test_rust_011_doc_comment_mentioning_cfg_feature_is_not_a_violation(self) -> None:
        """Regression for round-2A defect 2: `crates/private/template-json/
        src/lib.rs` in the real template repo has a doc comment mentioning
        `cfg(feature = "json")` to explain the amalgam-wiring pattern; the
        crate itself has no real `cfg(feature = ...)`. `has_cfg_feature`
        must ignore comment content."""

        repo = fixture("RUST-011", "green-doc-comment")
        ci, _ = load_ci_toml(repo)
        crates = discover_workspace(repo)
        self.assertEqual([], check_rust_011(ci, crates, repo))


class CargoScanUnitTest(unittest.TestCase):
    def test_expected_target_names_naming_scheme(self) -> None:
        from ci_lint.cargo_scan import expected_target_names

        repo = fixture("RUST-012", "green")
        crates = discover_workspace(repo)
        names = expected_target_names(crates[0])
        self.assertEqual({"demo-core:lib": "lib"}, names)


if __name__ == "__main__":
    unittest.main()
