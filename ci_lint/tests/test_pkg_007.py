"""PKG-007 (zackees/ci.yml#19): a release Linux (`*-unknown-linux-gnu`)
wheel must go through 'soldr wheel --release' (never a bare maturin --zig
cross path), never build on an arm64 runner, always carry a glibc
verification step after the build, and never pass --host-glibc without a
recorded [[exceptions]] entry."""

from __future__ import annotations

import unittest

from ci_lint.rules.packaging import check_pkg_007
from ci_lint.tests.helpers import fixture, requires_yaml_tooling


class Pkg007FixtureTest(unittest.TestCase):
    @requires_yaml_tooling
    def test_red_flags_every_condition(self) -> None:
        findings = check_pkg_007(fixture("PKG-007", "red"))
        rules = [f.rule for f in findings]
        self.assertEqual(["PKG-007"] * 4, rules)
        messages = "\n".join(f.message for f in findings)
        self.assertIn("maturin", messages.lower())
        self.assertIn("arm64", messages.lower())
        self.assertIn("verifies", messages.lower())
        self.assertIn("--host-glibc", messages)

    @requires_yaml_tooling
    def test_green_soldr_wheel_release_with_verify_is_clean(self) -> None:
        self.assertEqual([], check_pkg_007(fixture("PKG-007", "green")))


if __name__ == "__main__":
    unittest.main()
