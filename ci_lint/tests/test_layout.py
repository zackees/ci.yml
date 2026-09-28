"""LAYOUT-001 -- ci_lint/rules/layout.py. Pure filesystem + regex; no YAML
tooling needed."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ci_lint.rules.layout import check_group6
from ci_lint.schema import load_ci_toml
from ci_lint.tests.helpers import fixture, init_git_repo


class LayoutFixtureTest(unittest.TestCase):
    def test_layout_001_host_selector_outside_facade(self) -> None:
        repo = fixture("LAYOUT-001", "red")
        ci, _ = load_ci_toml(repo)
        self.assertIn("LAYOUT-001", [f.rule for f in check_group6(ci, repo)])

        repo = fixture("LAYOUT-001", "green")
        ci, _ = load_ci_toml(repo)
        self.assertNotIn("LAYOUT-001", [f.rule for f in check_group6(ci, repo)])

    def test_layout_001_ignores_gitignored_vendored_code(self) -> None:
        """Regression for round-2A defect 1: a gitignored
        `.cargo/registry/**` vendored dependency must never be scanned.
        Round-1A's filesystem-walk scan had no `.cargo` exclusion at all, so
        a real checkout's `.cargo/registry/**` produced 1,750 bogus
        LAYOUT-001 findings against the template repo. Built as a throwaway
        git repo (not a static checked-in fixture) because a genuinely
        gitignored file can never be committed to ci.yml's own repository
        without defeating the point of the test."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_git_repo(root)
            (root / ".gitignore").write_text("target/\n.cargo/registry/\n", encoding="utf-8")
            vendored = root / ".cargo" / "registry" / "src" / "bogus-dep-1.0.0"
            vendored.mkdir(parents=True)
            (vendored / "lib.rs").write_text(
                "#[cfg(windows)]\nfn vendored_windows_only() {}\n", encoding="utf-8"
            )
            repo = fixture("LAYOUT-001", "green")
            ci, _ = load_ci_toml(repo)
            findings = check_group6(ci, root)
        self.assertEqual([], findings)

    def test_layout_001_comment_mentioning_a_selector_is_not_a_violation(self) -> None:
        """Regression for round-2A defect 2: a doc comment merely
        mentioning `#[cfg(target_os = ...)]` must not be flagged as though
        it were real code outside the platform facade."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "crates" / "demo" / "src").mkdir(parents=True)
            (root / "crates" / "demo" / "src" / "lib.rs").write_text(
                '//! supports #[cfg(target_os = "linux")] per platform\n'
                "/// see also cfg(feature = \"json\")\n"
                "fn real() {}\n",
                encoding="utf-8",
            )
            repo = fixture("LAYOUT-001", "green")
            ci, _ = load_ci_toml(repo)

            # Reuse the green fixture's ci.toml (a valid, empty allowlist
            # match for this new path) against the temp-dir source tree.
            findings = check_group6(ci, root)
        self.assertEqual([], findings)

    def test_layout_001_python_comment_mentioning_sys_platform_is_not_a_violation(self) -> None:
        """Round-2A brief, defect 2: a `# ... sys.platform ...` Python
        comment must not be flagged as a real `sys.platform` use."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir(parents=True)
            (root / "src" / "app.py").write_text(
                "# sys.platform checks live in platforms/, not here\n"
                "def real():\n"
                "    return 1\n",
                encoding="utf-8",
            )
            repo = fixture("LAYOUT-001", "green")
            ci, _ = load_ci_toml(repo)
            findings = check_group6(ci, root)
        self.assertEqual([], findings)


if __name__ == "__main__":
    unittest.main()
