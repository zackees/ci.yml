"""CACHE-025 -- ci_lint/rules/swatinem_ban.py (zackees/ci.yml#209): a
`uses: Swatinem/rust-cache@...` step is banned; Rust build caching goes
through setup-soldr / soldr."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from ci_lint.finding import Status
from ci_lint.precheck import run_precheck
from ci_lint.rules.swatinem_ban import check_cache_025, scan_text
from ci_lint.tests.helpers import fixture, requires_yaml_tooling


class Cache025Test(unittest.TestCase):
    def test_green_setup_soldr_and_comments_are_clean(self) -> None:
        self.assertEqual([], check_cache_025(fixture("CACHE-025", "green")))

    def test_green_same_line_allow_with_reason(self) -> None:
        self.assertEqual([], check_cache_025(fixture("CACHE-025", "green-allow")))

    def test_red_workflow_steps(self) -> None:
        findings = check_cache_025(fixture("CACHE-025", "red"))
        # The second step's bare `allow CACHE-025` carries no reason, so it
        # does not excuse the line; the quoted, SHA-pinned, lower-case form
        # still matches.
        self.assertEqual(
            [("CACHE-025", ".github/workflows/ci.yml", 8), ("CACHE-025", ".github/workflows/ci.yml", 17)],
            [(f.rule, f.path, f.line) for f in findings],
        )
        self.assertTrue(all(f.status == Status.VIOLATION for f in findings))
        self.assertIn("setup-soldr", findings[0].fix)

    def test_red_composite_action(self) -> None:
        findings = check_cache_025(fixture("CACHE-025", "red-action"))
        self.assertEqual([(".github/actions/rust/action.yml", 5)], [(f.path, f.line) for f in findings])

    def test_lookalike_actions_do_not_match(self) -> None:
        text = "steps:\n  - uses: Swatinem/rust-cache-fork@v1\n  - uses: actions/cache@v4\n"
        self.assertEqual([], scan_text(text, "w.yml"))

    @requires_yaml_tooling
    def test_precheck_reports_cache_025(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            shutil.copytree(fixture("_e2e", "green"), root)
            action_dir = root / ".github" / "actions" / "swatinem"
            action_dir.mkdir(parents=True)
            shutil.copy(fixture("CACHE-025", "red-action") / ".github" / "actions" / "rust" / "action.yml", action_dir)
            result = run_precheck(root, title="[ci-windows] x")
        self.assertIn("CACHE-025", {f.rule for f in result.findings})


if __name__ == "__main__":
    unittest.main()
