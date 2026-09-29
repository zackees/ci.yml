"""RUST-016 -- ci_lint/rules/rust_dylint_target.py (zackees/ci.yml#82, M2-40)."""

from __future__ import annotations

import unittest

from ci_lint.finding import Status
from ci_lint.rules.rust_dylint_target import check_rust_016
from ci_lint.tests.helpers import fixture, requires_yaml_tooling


@requires_yaml_tooling
class Rust016Test(unittest.TestCase):
    def test_green_fixtures_are_clean(self) -> None:
        for kind in ("green-shared", "green-manifest", "green-allow", "green-script"):
            with self.subTest(kind=kind):
                self.assertEqual([], check_rust_016(fixture("RUST-016", kind)))

    def test_red_shared_target_dir(self) -> None:
        findings = check_rust_016(fixture("RUST-016", "red-shared"))
        self.assertEqual(1, len(findings))
        self.assertEqual((Status.VIOLATION, "RUST-016"), (findings[0].status, findings[0].rule))
        self.assertIn("shares target dir '/tmp/dylint-target'", findings[0].message)

    def test_red_manifest_path_ignores_crate_config(self) -> None:
        findings = check_rust_016(fixture("RUST-016", "red-manifest"))
        self.assertEqual(1, len(findings))
        self.assertIn("lints/foo/.cargo/config.toml is silently ignored", findings[0].message)

    def test_red_script_one_level_deep(self) -> None:
        findings = check_rust_016(fixture("RUST-016", "red-script"))
        messages = [f.message for f in findings]
        self.assertTrue(all(f.path == "ci/fixtures.py" and f.line == 3 for f in findings), findings)
        # both shapes: config ignored, and the default (inherited) target dir is the Dylint one
        self.assertTrue(any("silently ignored" in m for m in messages), messages)


if __name__ == "__main__":
    unittest.main()
