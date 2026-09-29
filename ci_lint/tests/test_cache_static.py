"""CACHE-001, CACHE-002, CACHE-003, CACHE-004 -- ci_lint/rules/cache_static.py."""

from __future__ import annotations

import unittest

from ci_lint.rules.cache_static import (
    check_cache_001,
    check_cache_002,
    check_cache_003_setup_uv,
    check_cache_004,
    check_cache_010,
    parse_size,
)
from ci_lint.schema import load_ci_toml
from ci_lint.tests.helpers import fixture, requires_yaml_tooling


class CacheStaticFixtureTest(unittest.TestCase):
    @requires_yaml_tooling
    def test_cache_001_raw_actions_cache(self) -> None:
        repo = fixture("CACHE-001", "red")
        ci, _ = load_ci_toml(repo)
        self.assertIn("CACHE-001", [f.rule for f in check_cache_001(ci, repo)])

        repo = fixture("CACHE-001", "green")
        ci, _ = load_ci_toml(repo)
        self.assertNotIn("CACHE-001", [f.rule for f in check_cache_001(ci, repo)])

    @requires_yaml_tooling
    def test_cache_001_wrapper_directory_is_sanctioned(self) -> None:
        """Round-4B: actions/cache is allowed ONLY inside
        [allow].cache-actions.only-in (default .github/actions/cache) --
        the SAME raw action used from a DIFFERENT composite-action
        directory is still CACHE-001."""

        repo = fixture("CACHE-001", "green-wrapper")
        ci, _ = load_ci_toml(repo)
        self.assertNotIn("CACHE-001", [f.rule for f in check_cache_001(ci, repo)])

        repo = fixture("CACHE-001", "red-outside-wrapper")
        ci, _ = load_ci_toml(repo)
        self.assertIn("CACHE-001", [f.rule for f in check_cache_001(ci, repo)])

    @requires_yaml_tooling
    def test_cache_002_volatile_key_component(self) -> None:
        repo = fixture("CACHE-002", "red")
        ci, _ = load_ci_toml(repo)
        self.assertIn("CACHE-002", [f.rule for f in check_cache_002(ci, repo)])

        repo = fixture("CACHE-002", "green")
        ci, _ = load_ci_toml(repo)
        self.assertNotIn("CACHE-002", [f.rule for f in check_cache_002(ci, repo)])

    @requires_yaml_tooling
    def test_cache_002_wrapper_key_must_be_dynamic(self) -> None:
        """Round-4B: inside the sanctioned actions/cache wrapper, a literal
        'key:' (even with no volatile token) is CACHE-002 -- keys must come
        from 'ci_lint cache key' via a step output or an input passthrough."""

        repo = fixture("CACHE-002", "red-wrapper-literal-key")
        ci, _ = load_ci_toml(repo)
        self.assertIn("CACHE-002", [f.rule for f in check_cache_002(ci, repo)])

        repo = fixture("CACHE-002", "green-wrapper-dynamic-key")
        ci, _ = load_ci_toml(repo)
        self.assertNotIn("CACHE-002", [f.rule for f in check_cache_002(ci, repo)])

    @requires_yaml_tooling
    def test_cache_003_setup_uv_save_cache_must_be_plan_driven(self) -> None:
        """Round-4B: astral-sh/setup-uv's 'save-cache' input, when
        [allow].setup-uv.require says it must be 'plan'-derived, is
        CACHE-003 if missing or a literal 'true'."""

        repo = fixture("CACHE-003", "red")
        ci, _ = load_ci_toml(repo)
        self.assertIn("CACHE-003", [f.rule for f in check_cache_003_setup_uv(ci, repo)])

        repo = fixture("CACHE-003", "green")
        ci, _ = load_ci_toml(repo)
        self.assertNotIn("CACHE-003", [f.rule for f in check_cache_003_setup_uv(ci, repo)])

    @requires_yaml_tooling
    def test_cache_010_kill_switch_env_while_family_declared_active(self) -> None:
        """Issue #7: ZCCACHE_DISABLE=1 in a job env, while ci.toml declares
        a [cache.family] via setup-soldr, is CACHE-010 (zackees/clud's
        `_dylint.yml` -- clud#487)."""

        repo = fixture("CACHE-010", "red")
        ci, _ = load_ci_toml(repo)
        self.assertIn("CACHE-010", [f.rule for f in check_cache_010(ci, repo)])

        repo = fixture("CACHE-010", "green")
        ci, _ = load_ci_toml(repo)
        self.assertNotIn("CACHE-010", [f.rule for f in check_cache_010(ci, repo)])

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

    def test_cache_004_full_pre_prune_removes_only_the_lockfile_peak_term(self) -> None:
        """Regression for round-2A defect 4: every writer flow setting
        `pre-prune = true` must remove only the lockfile-change-peak term
        from the worst-case sum, not waive the whole CACHE-004 proof. The
        CACHE-004/green fixture already has `pre-prune = true` on both
        writer flows (`flow.main`, `flow.nightly`); its steady total is
        50MB and `[cache.pr].budget` is 1GB, so `worst = steady +
        pr.budget` = ~1.074GB. Before the fix, the old code treated full
        pre-prune as "skip the check entirely" and never compared that sum
        against the budget at all."""

        from dataclasses import replace

        ci, _ = load_ci_toml(fixture("CACHE-004", "green"))
        self.assertTrue(ci.flows["main"].pre_prune)
        self.assertTrue(ci.flows["nightly"].pre_prune)

        # worst_pruned (steady 50MB + pr.budget 1GB) = ~1.074GB > 1GB budget:
        # the fixed formula must still fail, even though every writer flow
        # pre-prunes.
        tight = replace(ci, cache=replace(ci.cache, budget="1GB"))
        findings, arithmetic = check_cache_004(tight)
        self.assertIn("CACHE-004", [f.rule for f in findings], msg=arithmetic)
        self.assertIn("lockfile peak waived", arithmetic)

        # Raise the budget above worst_pruned (but still below worst_unpruned,
        # i.e. below steady + lockfile-peak + pr.budget = ~1.104GB) to prove
        # the lockfile-peak term really was dropped, not just shrunk.
        just_enough = replace(ci, cache=replace(ci.cache, budget="1100MB"))
        findings2, arithmetic2 = check_cache_004(just_enough)
        self.assertNotIn("CACHE-004", [f.rule for f in findings2], msg=arithmetic2)

    def test_cache_004_partial_pre_prune_still_counts_the_lockfile_peak(self) -> None:
        from dataclasses import replace

        ci, _ = load_ci_toml(fixture("CACHE-004", "green"))
        one_flow_only = replace(ci, flows={**ci.flows, "nightly": replace(ci.flows["nightly"], pre_prune=False)})
        findings, arithmetic = check_cache_004(replace(one_flow_only, cache=replace(one_flow_only.cache, budget="1100MB")))
        self.assertIn("CACHE-004", [f.rule for f in findings], msg=arithmetic)
        self.assertIn("not every writer flow pre-prunes", arithmetic)


class SizeParsingUnitTest(unittest.TestCase):
    def test_parse_size(self) -> None:
        self.assertEqual(9 * 1024**3, parse_size("9GB"))
        self.assertEqual(150 * 1024**2, parse_size("150MB"))
        self.assertIsNone(parse_size("not-a-size"))


if __name__ == "__main__":
    unittest.main()
