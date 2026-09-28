"""CACHE-001, CACHE-002, CACHE-004 -- ci_lint/rules/cache_static.py."""

from __future__ import annotations

import unittest

from ci_lint.rules.cache_static import check_cache_001, check_cache_002, check_cache_004, parse_size
from ci_lint.schema import load_ci_toml
from ci_lint.tests.helpers import fixture, requires_yaml_tooling


class CacheStaticFixtureTest(unittest.TestCase):
    @requires_yaml_tooling
    def test_cache_001_raw_actions_cache(self) -> None:
        self.assertIn("CACHE-001", [f.rule for f in check_cache_001(fixture("CACHE-001", "red"))])
        self.assertNotIn("CACHE-001", [f.rule for f in check_cache_001(fixture("CACHE-001", "green"))])

    @requires_yaml_tooling
    def test_cache_002_volatile_key_component(self) -> None:
        repo = fixture("CACHE-002", "red")
        ci, _ = load_ci_toml(repo)
        self.assertIn("CACHE-002", [f.rule for f in check_cache_002(ci, repo)])

        repo = fixture("CACHE-002", "green")
        ci, _ = load_ci_toml(repo)
        self.assertNotIn("CACHE-002", [f.rule for f in check_cache_002(ci, repo)])

    def test_cache_004_budget_exceeded_without_pre_prune(self) -> None:
        ci, _ = load_ci_toml(fixture("CACHE-004", "red"))
        findings, arithmetic = check_cache_004(ci)
        self.assertIn("CACHE-004", [f.rule for f in findings])
        self.assertIn("worst case", arithmetic)

        ci, _ = load_ci_toml(fixture("CACHE-004", "green"))
        findings, arithmetic = check_cache_004(ci)
        self.assertNotIn("CACHE-004", [f.rule for f in findings])

    def test_cache_004_budget_over_10gb_is_always_a_violation(self) -> None:
        ci, _ = load_ci_toml(fixture("CACHE-004", "green"))
        from dataclasses import replace

        big = replace(ci.cache, budget="11GB")
        ci_big = replace(ci, cache=big)
        findings, _ = check_cache_004(ci_big)
        self.assertIn("CACHE-004", [f.rule for f in findings])


class SizeParsingUnitTest(unittest.TestCase):
    def test_parse_size(self) -> None:
        self.assertEqual(9 * 1024**3, parse_size("9GB"))
        self.assertEqual(150 * 1024**2, parse_size("150MB"))
        self.assertIsNone(parse_size("not-a-size"))


if __name__ == "__main__":
    unittest.main()
