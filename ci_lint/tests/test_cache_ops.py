"""`ci-lint cache trim|janitor|heal|preprune` -- ci_lint.cache.ops
(round-4A brief, deliverable 5). No network: fake fetch/delete/graphql
record calls instead of touching the API; every test asserts `--dry-run`
(or omitting the delete function) never calls delete.
"""

from __future__ import annotations

import json
import unittest

from ci_lint.cache.ops import heal, janitor, preprune, trim
from ci_lint.github_api import GitHubApiError
from ci_lint.schema import load_ci_toml
from ci_lint.tests.helpers import FIXTURES

CACHE_FIXTURES = FIXTURES / "runtime" / "cache"
REPO = CACHE_FIXTURES / "repo"
TOKEN = "t"
REPO_SLUG = "zackees/template-python-rust-cmd"


def _load(name: str) -> dict[str, object]:
    return json.loads((CACHE_FIXTURES / name).read_text(encoding="utf-8"))


def _fetch_from(payload: dict[str, object]):
    def fetch(url: str, token: str):
        assert token
        return payload

    return fetch


def _graphql_from(payload: dict[str, object]):
    def graphql(query: str, token: str):
        assert token
        return payload

    return graphql


def _recording_delete(calls: list[str]):
    def delete(url: str, token: str) -> None:
        assert token
        calls.append(url)

    return delete


def _failing_delete():
    def delete(url: str, token: str) -> None:
        raise GitHubApiError("boom")

    return delete


class TrimTest(unittest.TestCase):
    def setUp(self) -> None:
        ci, findings = load_ci_toml(REPO)
        assert ci is not None, findings
        self.ci = ci
        self.fetch = _fetch_from(_load("caches-synthetic-rules.json"))
        self.graphql = _graphql_from(_load("pr-states.json"))

    def test_dry_run_plans_both_cache_008_deltas_but_deletes_nothing(self) -> None:
        result = trim(
            self.ci, fetch=self.fetch, graphql=self.graphql, delete=None, token=TOKEN, repo=REPO_SLUG,
            dry_run=True,
        )
        self.assertTrue(result.dry_run)
        self.assertEqual((), result.deleted)
        planned_ids = {p.id for p in result.planned}
        self.assertEqual({9001, 9002}, planned_ids)

    def test_live_run_deletes_exactly_the_planned_ids(self) -> None:
        calls: list[str] = []
        result = trim(
            self.ci, fetch=self.fetch, graphql=self.graphql, delete=_recording_delete(calls),
            token=TOKEN, repo=REPO_SLUG, dry_run=False,
        )
        self.assertFalse(result.dry_run)
        self.assertEqual({9001, 9002}, {p.id for p in result.deleted})
        self.assertEqual(2, len(calls))
        self.assertTrue(all("actions/caches/900" in c for c in calls))

    def test_max_deletes_caps_the_plan(self) -> None:
        result = trim(
            self.ci, fetch=self.fetch, graphql=self.graphql, delete=None, token=TOKEN, repo=REPO_SLUG,
            dry_run=True, max_deletes=1,
        )
        self.assertEqual(1, len(result.planned))

    def test_never_deletes_a_non_cache_008_entry(self) -> None:
        result = trim(
            self.ci, fetch=self.fetch, graphql=self.graphql, delete=None, token=TOKEN, repo=REPO_SLUG,
            dry_run=True, max_deletes=100,
        )
        planned_ids = {p.id for p in result.planned}
        self.assertNotIn(9003, planned_ids)  # CACHE-009 (retired), not CACHE-008 -- trim's job, not janitor's
        self.assertNotIn(9004, planned_ids)  # CACHE-003
        self.assertNotIn(9005, planned_ids)  # CACHE-005

    def test_delete_error_is_recorded_not_raised(self) -> None:
        result = trim(
            self.ci, fetch=self.fetch, graphql=self.graphql, delete=_failing_delete(), token=TOKEN,
            repo=REPO_SLUG, dry_run=False,
        )
        self.assertEqual(2, len(result.errors))
        self.assertEqual((), result.deleted)


class JanitorTest(unittest.TestCase):
    def setUp(self) -> None:
        ci, findings = load_ci_toml(REPO)
        assert ci is not None, findings
        self.ci = ci
        self.fetch = _fetch_from(_load("caches-synthetic-rules.json"))
        self.graphql = _graphql_from(_load("pr-states.json"))

    def test_deletes_cache_003_005_009_but_not_cache_008(self) -> None:
        result, table = janitor(
            self.ci, fetch=self.fetch, graphql=self.graphql, delete=None, token=TOKEN, repo=REPO_SLUG,
            dry_run=True, stale_days=999999,  # disable the stale-entry sweep for this assertion
        )
        planned_ids = {p.id for p in result.planned}
        self.assertEqual({9003, 9004, 9005}, planned_ids)
        self.assertIn("family", table)

    def test_stale_entries_are_swept_too(self) -> None:
        # every synthetic entry's last_accessed_at is 2026-09-2*; "now" far
        # in the future makes all of them stale.
        import datetime

        far_future = datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc).timestamp()
        result, _table = janitor(
            self.ci, fetch=self.fetch, graphql=self.graphql, delete=None, token=TOKEN, repo=REPO_SLUG,
            dry_run=True, stale_days=5, now=far_future, max_deletes=100,
        )
        planned_ids = {p.id for p in result.planned}
        # 8237210113 (the live "compile" base entry) is also in this fixture and is stale too.
        self.assertIn(8237210113, planned_ids)

    def test_before_after_table_reflects_a_live_delete(self) -> None:
        calls: list[str] = []
        result, table = janitor(
            self.ci, fetch=self.fetch, graphql=self.graphql, delete=_recording_delete(calls), token=TOKEN,
            repo=REPO_SLUG, dry_run=False, stale_days=999999,
        )
        self.assertEqual(3, len(result.deleted))
        self.assertIn("retired", table)  # id 9003's family label


class HealTest(unittest.TestCase):
    def test_dry_run_never_deletes(self) -> None:
        result = heal(delete=None, token=TOKEN, repo=REPO_SLUG, key="some-poisoned-key", dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertEqual((), result.deleted)
        self.assertEqual(1, len(result.planned))

    def test_live_run_deletes_by_key_with_no_id_lookup(self) -> None:
        calls: list[str] = []
        result = heal(delete=_recording_delete(calls), token=TOKEN, repo=REPO_SLUG, key="some-poisoned-key")
        self.assertEqual(1, len(result.deleted))
        self.assertEqual(1, len(calls))
        self.assertIn("key=some-poisoned-key", calls[0])

    def test_delete_failure_is_recorded(self) -> None:
        result = heal(delete=_failing_delete(), token=TOKEN, repo=REPO_SLUG, key="k")
        self.assertEqual(1, len(result.errors))


class PrepruneTest(unittest.TestCase):
    def setUp(self) -> None:
        ci, findings = load_ci_toml(REPO)
        assert ci is not None, findings
        self.ci = ci

    def test_under_budget_without_lockfile_change_prunes_nothing(self) -> None:
        result, summary = preprune(
            self.ci, fetch=_fetch_from(_load("caches-live-template.json")), graphql=None, delete=None,
            token=TOKEN, repo=REPO_SLUG, lockfile_changed=False, dry_run=True,
        )
        self.assertEqual((), result.planned)
        self.assertIn("under budget", summary)

    def test_over_budget_with_lockfile_change_prunes_the_superseded_lockfile_family_entry(self) -> None:
        from dataclasses import replace

        ci_tiny_budget = replace(self.ci, cache=replace(self.ci.cache, budget="1B"))
        result, summary = preprune(
            ci_tiny_budget, fetch=_fetch_from(_load("caches-live-template.json")), graphql=None, delete=None,
            token=TOKEN, repo=REPO_SLUG, lockfile_changed=True, dry_run=True,
        )
        self.assertIn("OVER budget", summary)
        # the superseded (CACHE-006) "registry" entry is lockfile-keyed and about to be rewritten.
        self.assertEqual(1, len(result.planned))
        self.assertEqual(8237210495, result.planned[0].id)


if __name__ == "__main__":
    unittest.main()
