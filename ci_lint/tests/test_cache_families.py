"""`ci_lint.cache.families` -- the `via` -> key-prefix resolution table
(round-4A brief, deliverable 1)."""

from __future__ import annotations

import unittest

from ci_lint.cache.families import (
    ALLOWED_VIA_VALUES,
    resolve_prefix,
    resolve_prefixes,
    resolve_retired_prefix,
)


class FamilyResolutionTest(unittest.TestCase):
    def test_every_shape_from_the_round_4a_brief(self) -> None:
        # DESIGN SOURCE table, verbatim (round-4A brief, deliverable 1),
        # plus "setup-uv" (same-round amendment).
        expected = {
            "setup-soldr:build-cache": "setup-soldr-buildcache-v2-",
            "setup-soldr:cargo-registry": "setup-soldr-cargoregistry-v1-",
            "setup-soldr:cook": "cook-base-v2-",
            "setup-soldr:cross-targets": "setup-soldr-prepare-v3-",
            "setup-soldr:dylint": "setup-soldr-dylint-v2-",
            "setup-soldr:dylint-output": "setup-soldr-dylint-output-v2-",
            "setup-soldr:soldr-mini": "soldr-mini-v2-",
            "setup-soldr:solo-toolchain": "solo-toolchain-v3-",
            "setup-soldr:cook-delta": "cook-delta-v2-",
            "setup-uv": "setup-uv-2-",
        }
        for via, prefix in expected.items():
            with self.subTest(via=via):
                self.assertEqual(prefix, resolve_prefix(via, "irrelevant-family-id"))

    def test_ci_lint_via_uses_the_familys_own_id(self) -> None:
        self.assertEqual("uv-v1-", resolve_prefix("ci-lint", "uv"))
        self.assertEqual("compile-v1-", resolve_prefix("ci-lint", "compile"))

    def test_setup_uv_accepts_the_v6_legacy_generation(self) -> None:
        # zackees/ci.yml#87: building keys uses the current generation only.
        self.assertEqual(("setup-uv-2-", "setup-uv-1-"), resolve_prefixes("setup-uv", "uv"))
        self.assertEqual(("uv-v1-",), resolve_prefixes("ci-lint", "uv"))
        self.assertEqual((), resolve_prefixes("bogus:family", "x"))

    def test_unknown_via_resolves_to_none(self) -> None:
        self.assertIsNone(resolve_prefix("bogus:family", "x"))

    def test_allowed_via_values_matches_every_shape_plus_ci_lint(self) -> None:
        self.assertIn("ci-lint", ALLOWED_VIA_VALUES)
        self.assertIn("setup-soldr:soldr-mini", ALLOWED_VIA_VALUES)
        self.assertIn("setup-soldr:dylint", ALLOWED_VIA_VALUES)
        self.assertIn("setup-uv", ALLOWED_VIA_VALUES)
        self.assertNotIn("bogus:family", ALLOWED_VIA_VALUES)

    def test_retired_prefix_matches_a_live_key_with_the_same_start(self) -> None:
        self.assertTrue("solo-toolchain-v3-linux-x64-glibc-rustc1.95.0".startswith(resolve_retired_prefix("solo-toolchain")))
        self.assertTrue("cook-delta-v2-linux-x64-whatever".startswith(resolve_retired_prefix("cook-delta-v2")))
        self.assertFalse("cook-delta-v3-linux-x64-whatever".startswith(resolve_retired_prefix("cook-delta-v2")))


if __name__ == "__main__":
    unittest.main()
