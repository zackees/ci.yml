"""`ci-lint example drift` -- ci_lint/example_drift.py (round-6C, item 3).

Uses `ci_lint/tests/fixtures/runtime/example-drift/`: one canonical
`example.toml`, a `green/ci.toml` that differs only in the round rule's
declared repo-specific ways, and a `red/ci.toml` that additionally
changes `[lint.dylint].shape` (a D6 decision, not a repo identifier),
drops `[suites.unit].required` to false (only `.run` is allowed to
differ), and adds a whole extra top-level `[extra]` table.
"""

from __future__ import annotations

import unittest

from ci_lint.example_drift import ExampleDriftError, compute_example_drift, render_text
from ci_lint.tests.helpers import FIXTURES


class ExampleDriftTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = FIXTURES / "runtime" / "example-drift"
        self.example = self.dir / "example.toml"

    # -- GREEN: only the round rule's allowed repo-specific fields differ -

    def test_green_repo_has_no_policy_drift(self) -> None:
        entries = compute_example_drift(self.example, self.dir / "green" / "ci.toml")
        drift = [e for e in entries if not e.repo_specific]
        self.assertEqual([], drift, msg=[(e.path, e.example, e.repo) for e in drift])
        # It's not literally identical -- every allowed field really did
        # differ, and each one is classified repo-specific.
        self.assertTrue(entries)
        self.assertTrue(all(e.repo_specific for e in entries))

    def test_green_covers_every_allowed_category(self) -> None:
        entries = compute_example_drift(self.example, self.dir / "green" / "ci.toml")
        paths = {e.path for e in entries}
        self.assertIn("linter", paths)
        self.assertIn("platforms.linux-x64.runs-on", paths)
        self.assertIn("platforms.linux-x64.wheel", paths)
        self.assertIn("rust.public", paths)
        self.assertIn("rust.private", paths)
        self.assertIn("rust.ship", paths)
        self.assertIn("rust.tests.binaries", paths)
        self.assertIn("allow.platform-selector", paths)
        self.assertIn("allow.platform-code", paths)
        self.assertIn("suites.unit.run", paths)
        self.assertIn("cache.family.registry.max", paths)
        self.assertIn("python.cli.name", paths)
        self.assertIn("python.cli.crate", paths)
        # The whole [[exceptions]] array differs (an extra field added) --
        # one entry rooted at "exceptions".
        self.assertIn("exceptions", paths)

    # -- RED: everything the green fixture has, plus three non-exempt ----

    def test_red_repo_flags_dylint_shape_as_policy_drift(self) -> None:
        entries = compute_example_drift(self.example, self.dir / "red" / "ci.toml")
        hit = next(e for e in entries if e.path == "lint.dylint.shape")
        self.assertFalse(hit.repo_specific)
        self.assertEqual("measure", hit.example)
        self.assertEqual("multi-target", hit.repo)

    def test_red_repo_flags_suite_required_as_policy_drift(self) -> None:
        entries = compute_example_drift(self.example, self.dir / "red" / "ci.toml")
        hit = next(e for e in entries if e.path == "suites.unit.required")
        self.assertFalse(hit.repo_specific)
        self.assertTrue(hit.example)
        self.assertFalse(hit.repo)
        # But suites.unit.run (the actual test-invocation command) is
        # still allowed to differ in the same fixture.
        run_entry = next(e for e in entries if e.path == "suites.unit.run")
        self.assertTrue(run_entry.repo_specific)

    def test_red_repo_flags_extra_top_level_table_as_policy_drift(self) -> None:
        entries = compute_example_drift(self.example, self.dir / "red" / "ci.toml")
        hit = next(e for e in entries if e.path == "extra")
        self.assertFalse(hit.repo_specific)
        self.assertEqual("extra_in_repo", hit.kind)

    def test_red_repo_has_exactly_the_three_drift_entries(self) -> None:
        entries = compute_example_drift(self.example, self.dir / "red" / "ci.toml")
        drift_paths = sorted(e.path for e in entries if not e.repo_specific)
        self.assertEqual(["extra", "lint.dylint.shape", "suites.unit.required"], drift_paths)

    # -- Identity: comparing a file to itself is silent -------------------

    def test_example_against_itself_has_no_differences(self) -> None:
        entries = compute_example_drift(self.example, self.example)
        self.assertEqual((), entries)

    # -- Errors -------------------------------------------------------------

    def test_missing_example_file_raises(self) -> None:
        with self.assertRaises(ExampleDriftError):
            compute_example_drift(self.dir / "does-not-exist.toml", self.dir / "green" / "ci.toml")

    def test_missing_repo_file_raises(self) -> None:
        with self.assertRaises(ExampleDriftError):
            compute_example_drift(self.example, self.dir / "does-not-exist" / "ci.toml")

    def test_malformed_toml_raises(self) -> None:
        with self.assertRaises(ExampleDriftError):
            compute_example_drift(self.example, self.dir / "malformed.toml")

    def test_render_text_does_not_crash(self) -> None:
        entries = compute_example_drift(self.example, self.dir / "red" / "ci.toml")
        text = render_text(entries, example_path=self.example, repo_ci_toml=self.dir / "red" / "ci.toml")
        self.assertIn("SCHEMA/POLICY DRIFT", text)
        self.assertIn("lint.dylint.shape", text)


if __name__ == "__main__":
    unittest.main()
