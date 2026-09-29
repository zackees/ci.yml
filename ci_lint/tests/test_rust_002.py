"""RUST-002 -- ci_lint/rules/tools.py's check_rust_002 (round-6E).

zackees/ci.yml#1's acceptance criteria: Dylint invoked in more than one
job; Dylint in a non-Linux job; a bare cargo-dylint/dylint-link/`cargo
dylint` (not soldr's wrapper), in a run: line or one level deep into a
ci/*.py script; `cargo install cargo-dylint`/`dylint-link` (a per-run tool
build); and `--workspace` without `--all` (round-2B's cargo-dylint no-op
footgun -- "Nothing to do. Did you forget --all?").
"""

from __future__ import annotations

import unittest

from ci_lint.finding import Status
from ci_lint.rules.tools import check_rust_002
from ci_lint.tests.helpers import fixture, requires_yaml_tooling


def rule_ids(findings) -> list[str]:
    return [f.rule for f in findings]


@requires_yaml_tooling
class Rust002Test(unittest.TestCase):
    def test_green_single_linux_job_via_soldr_is_clean(self) -> None:
        findings = check_rust_002(fixture("RUST-002", "green"))
        self.assertEqual([], findings)

    def test_green_script_indirection_via_soldr_is_clean(self) -> None:
        findings = check_rust_002(fixture("RUST-002", "green-script-indirection"))
        self.assertEqual([], findings)

    def test_duplicate_jobs_is_rust_002(self) -> None:
        findings = check_rust_002(fixture("RUST-002", "red-duplicate-jobs"))
        rules = rule_ids(findings)
        self.assertEqual(2, rules.count("RUST-002"))
        self.assertTrue(all(f.status == Status.VIOLATION for f in findings))
        messages = " ".join(f.message for f in findings)
        self.assertIn("dylint-linux", messages)
        self.assertIn("dylint-macos", messages)

    def test_non_linux_runner_is_rust_002(self) -> None:
        findings = check_rust_002(fixture("RUST-002", "red-non-linux"))
        self.assertIn("RUST-002", rule_ids(findings))
        self.assertTrue(any("macos-15" in f.message for f in findings))
        self.assertTrue(all(f.status == Status.VIOLATION for f in findings))

    def test_bare_cargo_dylint_is_rust_002(self) -> None:
        findings = check_rust_002(fixture("RUST-002", "red-bare-cargo-dylint"))
        self.assertIn("RUST-002", rule_ids(findings))
        self.assertTrue(any("bypassing soldr" in f.message for f in findings))

    def test_cargo_install_dylint_tool_is_rust_002(self) -> None:
        findings = check_rust_002(fixture("RUST-002", "red-cargo-install"))
        self.assertIn("RUST-002", rule_ids(findings))
        self.assertTrue(any("builds a Dylint tool from source" in f.message for f in findings))

    def test_workspace_without_all_is_rust_002(self) -> None:
        findings = check_rust_002(fixture("RUST-002", "red-workspace-without-all"))
        self.assertIn("RUST-002", rule_ids(findings))
        self.assertTrue(any("Did you forget --all" in f.message for f in findings))

    def test_script_indirection_bare_dylint_is_rust_002(self) -> None:
        """The workflow's own run: line ('python3 ci/dylint.py ...') is
        clean; the violation is one level deep inside ci/dylint.py's own
        subprocess.run(['cargo-dylint', ...]) call."""

        findings = check_rust_002(fixture("RUST-002", "red-script-bare-dylint"))
        self.assertIn("RUST-002", rule_ids(findings))
        self.assertTrue(any("cargo-dylint" in f.message for f in findings))
        self.assertTrue(any(f.line is not None for f in findings))


if __name__ == "__main__":
    unittest.main()
