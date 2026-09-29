"""`ci-lint cache audit` -- ci_lint.cache.audit (round-4A brief, deliverable
4). No network: every test injects a fake fetch/graphql that reads the
recorded fixtures under ci_lint/tests/fixtures/runtime/cache/ (one copied
verbatim from the live template repo, one built for rule coverage the live
repo doesn't currently exhibit -- see caches-synthetic-rules.json's own
"_note").
"""

from __future__ import annotations

import json
import unittest

from ci_lint.cache.audit import audit_classified, classify, run_audit
from ci_lint.cache.github_cache import GitHubApiError, list_caches
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
        assert "actions/caches" in url
        return payload

    return fetch


def _graphql_from(payload: dict[str, object]):
    def graphql(query: str, token: str):
        assert token
        assert "pullRequest" in query
        return payload

    return graphql


class ClassifyLiveTemplateTest(unittest.TestCase):
    """caches-live-template.json is copied verbatim from `gh api repos/
    zackees/template-python-rust-cmd/actions/caches` (round-4A brief:
    "copy the JSON, don't mutate anything")."""

    def setUp(self) -> None:
        ci, findings = load_ci_toml(REPO)
        assert ci is not None, findings
        self.ci = ci
        payload = _load("caches-live-template.json")
        entries = list_caches(_fetch_from(payload), TOKEN, REPO_SLUG)
        self.classified = classify(self.ci, entries)
        self.by_id = {c.entry.id: c for c in self.classified}

    def test_soldr_mini_classifies_once_declared(self) -> None:
        c = self.by_id[8237216840]
        self.assertEqual("soldr-mini", c.family_id)
        self.assertFalse(c.is_delta)
        self.assertFalse(c.is_retired)

    def test_registry_and_compile_classify(self) -> None:
        self.assertEqual("registry", self.by_id[8237268308].family_id)
        self.assertEqual("deps", self.by_id[8237210664].family_id)
        self.assertEqual("compile", self.by_id[8237210113].family_id)
        self.assertEqual("compile", self.by_id[8237266493].family_id)  # the "-dylint-" suffixed shape too

    def test_setup_uv_own_cache_is_undeclared(self) -> None:
        # astral-sh/setup-uv's own cache mechanism -- a genuinely different
        # key shape from ci-lint's declared "uv" family (via = "ci-lint",
        # prefix "uv-v1-"); this is a REAL CACHE-001 finding, not a fixture
        # artifact.
        for cache_id in (8238163287, 8237209367, 8236693780):
            with self.subTest(cache_id=cache_id):
                self.assertIsNone(self.by_id[cache_id].family_id)
                self.assertFalse(self.by_id[cache_id].is_retired)

    def test_audit_findings_cache_001_and_cache_006(self) -> None:
        report = audit_classified(
            self.ci, self.classified, graphql=None, token=TOKEN, repo=REPO_SLUG, default_branch="main"
        )
        by_rule_id = {(f.rule, f.path) for f in report.findings}
        self.assertIn(("CACHE-001", "cache:" + self.by_id[8238163287].entry.key), by_rule_id)
        self.assertEqual(3, sum(1 for f in report.findings if f.rule == "CACHE-001"))
        # the older (less-recently-accessed) of the two "registry" entries
        # that differ only in their trailing lockfile-hash component
        self.assertIn(("CACHE-006", "cache:" + self.by_id[8237210495].entry.key), by_rule_id)
        self.assertNotIn(("CACHE-006", "cache:" + self.by_id[8237268308].entry.key), by_rule_id)
        self.assertEqual(250382443, report.total_bytes)
        self.assertEqual(0, sum(1 for f in report.findings if f.rule == "CACHE-003"))
        self.assertEqual(0, sum(1 for f in report.findings if f.rule == "CACHE-009"))


class ClassifySyntheticRulesTest(unittest.TestCase):
    """caches-synthetic-rules.json + pr-states.json (both synthetic, per
    their own "_note") exercise CACHE-003/005/008/009, which the live
    template repo does not currently exhibit."""

    def setUp(self) -> None:
        ci, findings = load_ci_toml(REPO)
        assert ci is not None, findings
        self.ci = ci
        payload = _load("caches-synthetic-rules.json")
        entries = list_caches(_fetch_from(payload), TOKEN, REPO_SLUG)
        self.classified = classify(self.ci, entries)
        self.report = audit_classified(
            self.ci,
            self.classified,
            graphql=_graphql_from(_load("pr-states.json")),
            token=TOKEN,
            repo=REPO_SLUG,
            default_branch="main",
        )
        self.by_rule_id = {(f.rule, f.path) for f in self.report.findings}

    def test_cache_003_base_layer_on_a_pr_ref(self) -> None:
        self.assertIn(("CACHE-003", "cache:setup-soldr-buildcache-v2-linux-x64-deadbeefdeadbeef-cafebabecafebabe"), self.by_rule_id)

    def test_cache_005_tiny_payload(self) -> None:
        self.assertIn(("CACHE-005", "cache:setup-soldr-cargoregistry-v1-linux-x64-deadbeefdeadbeef-cafebabecafebabe"), self.by_rule_id)

    def test_cache_008_closed_pr_delta(self) -> None:
        closed = [f for f in self.report.findings if f.rule == "CACHE-008" and "pr7" in f.path]
        self.assertEqual(1, len(closed))
        self.assertIn("closed", closed[0].message)

    def test_cache_008_stale_base_delta(self) -> None:
        # id 9002's key has no "pr7"/"pr8" substring conflict issue since we
        # match by path instead.
        stale = [
            f
            for f in self.report.findings
            if f.rule == "CACHE-008" and f.path == "cache:delta-v1-pr8-compile-linux-x64-bdeadbeef"
        ]
        self.assertEqual(1, len(stale))
        self.assertIn("base hash", stale[0].message)

    def test_cache_009_retired_family_live(self) -> None:
        self.assertIn(("CACHE-009", "cache:cook-delta-v2-linux-x64-glibc-rustc1.95.0-fnone-la87d2454829c34f1-soldrv0.9.25"), self.by_rule_id)

    def test_matching_delta_base_is_not_flagged_stale(self) -> None:
        # id 9001's b43cef837 DOES match the live "compile" base entry's
        # own key hash -- it must be reported via the "closed" reason only,
        # never also as a stale-base mismatch.
        for f in self.report.findings:
            if f.path == "cache:delta-v1-pr7-compile-linux-x64-b43cef837":
                self.assertIn("closed", f.message)


class RunAuditErrorHandlingTest(unittest.TestCase):
    def test_fetch_failure_raises_audit_error_not_a_crash(self) -> None:
        from ci_lint.cache.audit import AuditError

        ci, findings = load_ci_toml(REPO)
        assert ci is not None, findings

        def broken_fetch(url: str, token: str):
            raise GitHubApiError("network is down")

        with self.assertRaises(AuditError):
            run_audit(ci, fetch=broken_fetch, graphql=None, token=TOKEN, repo=REPO_SLUG)

    def test_graphql_failure_degrades_to_a_warning_not_a_crash(self) -> None:
        ci, findings = load_ci_toml(REPO)
        assert ci is not None, findings
        payload = _load("caches-synthetic-rules.json")

        def broken_graphql(query: str, token: str):
            raise GitHubApiError("graphql is down")

        report = run_audit(
            ci, fetch=_fetch_from(payload), graphql=broken_graphql, token=TOKEN, repo=REPO_SLUG
        )
        self.assertIsNotNone(report.warning)
        # CACHE-008's PR-state half degraded, but the base-hash-mismatch
        # half (which needs no GraphQL) must still have fired for PR #8's
        # deliberately-stale delta.
        self.assertTrue(
            any(f.rule == "CACHE-008" and "pr8" in f.path for f in report.findings if f.path)
        )


if __name__ == "__main__":
    unittest.main()
