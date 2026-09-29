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

from ci_lint.cache.audit import ClassifiedEntry, audit_classified, classify, run_audit
from ci_lint.cache.github_cache import CacheEntry, GitHubApiError, list_caches
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
        if "actions/runs" in url:
            # RUST-004 (round-6E): audit_classified/run_audit now always
            # probes the default branch's latest successful dylint run
            # when fetch is given and a dylint family is declared. None of
            # THIS module's other fixtures are about RUST-004, so answer
            # "no runs" here -- _find_default_branch_dylint_run then
            # returns None and RUST-004 stays silent, exactly like before
            # round-6E for every test that doesn't ask about it.
            return {"workflow_runs": []}
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

    def test_setup_uv_own_cache_classifies_once_declared(self) -> None:
        # round-4A amendment: astral-sh/setup-uv's own cache is now a
        # declared family in the fixture repo (via = "setup-uv", prefix
        # "setup-uv-2-") -- no longer a permanent CACHE-001. All three
        # entries in this fixture are the "setup-uv-2-..." shape.
        for cache_id in (8238163287, 8237209367, 8236693780):
            with self.subTest(cache_id=cache_id):
                c = self.by_id[cache_id]
                self.assertEqual("setup-uv-cache", c.family_id)
                self.assertFalse(c.is_delta)
                self.assertFalse(c.is_retired)

    def test_audit_findings_cache_001_cache_003_and_cache_006(self) -> None:
        report = audit_classified(
            self.ci, self.classified, graphql=None, token=TOKEN, repo=REPO_SLUG, default_branch="main"
        )

        # Two entries (8237209367 on refs/heads/main, 8236693780 on
        # refs/pull/17/merge) share an IDENTICAL key string, so `path`
        # ("cache:<key>") alone can't disambiguate which entry a finding is
        # about -- match on the SUBJECT "cache id=<N>" the message always
        # leads with instead (CACHE-006's message also mentions a second,
        # different id -- "kept newest id=<other>" -- so a bare substring
        # search on "id=<N>" would false-match that entry too).
        def findings_for(rule: str, cache_id: int) -> list:
            needle = f"cache id={cache_id} "
            return [f for f in report.findings if f.rule == rule and needle in f.message]

        # No more CACHE-001s: setup-uv-2-... is a declared family now.
        self.assertEqual(0, sum(1 for f in report.findings if f.rule == "CACHE-001"))
        # ...but two of its three entries live on refs/pull/*, exactly like
        # a base layer would -- CACHE-003, the same rule, no special case.
        self.assertEqual(1, len(findings_for("CACHE-003", 8238163287)))  # refs/pull/19/merge
        self.assertEqual(1, len(findings_for("CACHE-003", 8236693780)))  # refs/pull/17/merge
        self.assertEqual(0, len(findings_for("CACHE-003", 8237209367)))  # refs/heads/main
        self.assertEqual(2, sum(1 for f in report.findings if f.rule == "CACHE-003"))
        # zackees/ci.yml#88: the two "registry" entries share the Cargo.lock
        # hash (a87d2454829c34f1) and differ only in the trailing TOOLCHAIN
        # digest (5820... = the main build job, b72e... = the dylint job --
        # the same digests the two buildcache keys carry), so both are live
        # per-job entries, not generations. A real superseded registry
        # generation is covered by RegistryPerJobDigestTest below.
        self.assertEqual(0, len(findings_for("CACHE-006", 8237210495)))
        self.assertEqual(0, len(findings_for("CACHE-006", 8237268308)))
        # zackees/ci.yml#104: 8238163287 (23:42), 8237209367 (main, 23:37)
        # and 8236693780 (pr17, 22:47) are all the SAME "setup-uv-2-...
        # ubuntu-24.04-3.12.3" platform/python stem; only their trailing
        # 64-hex (sha256) dependency-file hash differs. Before #104's fix
        # `_shape` only stripped 6-40 hex chars, so the 64-hex tail was
        # never stripped and the older two never grouped with the newest
        # -- exactly the kernal-api "superseded setup-uv generation missed"
        # evidence. Now all three group together and both older-accessed
        # entries are superseded, keeping only the newest (8238163287).
        self.assertEqual(1, len(findings_for("CACHE-006", 8236693780)))
        self.assertEqual(1, len(findings_for("CACHE-006", 8237209367)))
        self.assertEqual(0, len(findings_for("CACHE-006", 8238163287)))
        self.assertEqual(250382443, report.total_bytes)
        self.assertEqual(0, sum(1 for f in report.findings if f.rule == "CACHE-009"))

    def test_gen_009_rollup_fires_when_cache_003_and_006_do(self) -> None:
        """RED: this fixture already produces CACHE-003 and CACHE-006
        findings (previous test) -- GEN-009 (issue #5) is the single
        fleet-level rollup a human/agent reads instead of grepping four
        rule IDs separately; it must fire here and cite both."""

        report = audit_classified(
            self.ci, self.classified, graphql=None, token=TOKEN, repo=REPO_SLUG, default_branch="main"
        )
        gen_009 = [f for f in report.findings if f.rule == "GEN-009"]
        self.assertEqual(1, len(gen_009), report.findings)
        self.assertIn("CACHE-003", gen_009[0].message)
        self.assertIn("CACHE-006", gen_009[0].message)

    def test_gen_009_absent_when_no_rollup_contributor_fires(self) -> None:
        """GREEN: an empty cache listing has nothing for CACHE-003/004/006/
        008 to flag, so GEN-009 must not fire either."""

        report = audit_classified(
            self.ci, (), graphql=None, token=TOKEN, repo=REPO_SLUG, default_branch="main"
        )
        self.assertEqual([], [f for f in report.findings if f.rule == "GEN-009"])


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


class ClassifyGenerationSuffixedDeltaTest(unittest.TestCase):
    """Round-4C: a delta key carries a `-g<sha8>` per-generation commit-sha
    suffix (template-python-rust-cmd#31 -- cache entries are immutable, so
    each save needs a distinct key; the previous generation is healed once
    a new one lands). `classify()` predates that suffix and, before this
    fix, silently fell through to CACHE-001 "undeclared family" instead of
    recognizing it as a delta at all -- which meant CACHE-008 (closed/
    merged-PR trim) never saw it either. RED: reverting DELTA_OUTER_RE's
    trailing `(?:-g...)?` group reproduces the miss (is_delta=False)."""

    def setUp(self) -> None:
        ci, findings = load_ci_toml(REPO)
        assert ci is not None, findings
        self.ci = ci

    def test_delimited_pr_delta_key_is_classified_as_a_delta(self) -> None:
        # zackees/ci.yml#23 §5: build_delta_key now emits `delta-v1-pr-<N>-...`.
        entry = CacheEntry(
            id=2,
            ref="refs/pull/36/merge",
            key="delta-v1-pr-36-compile-linux-x64-ba37ca145-g5601e597",
            version="v1",
            size_in_bytes=1_224_921,
            created_at="2026-09-29T02:49:27Z",
            last_accessed_at="2026-09-29T02:49:27Z",
        )
        (classified,) = classify(self.ci, [entry])
        self.assertTrue(classified.is_delta, "delimited pr-<N> delta key must classify as a delta, not CACHE-001")
        assert classified.delta is not None
        self.assertEqual(36, classified.delta.pr)
        self.assertEqual("compile", classified.delta.family)
        self.assertEqual("linux-x64", classified.delta.platform)
        self.assertEqual(36, classified.pr)

    def test_generation_suffixed_key_is_still_classified_as_a_delta(self) -> None:
        entry = CacheEntry(
            id=1,
            ref="refs/pull/31/merge",
            key="delta-v1-pr31-compile-linux-x64-ba37ca145-g5601e597",
            version="v1",
            size_in_bytes=1_224_921,
            created_at="2026-09-29T02:49:27Z",
            last_accessed_at="2026-09-29T02:49:27Z",
        )
        (classified,) = classify(self.ci, [entry])
        self.assertTrue(classified.is_delta, "generation-suffixed delta key must classify as a delta, not CACHE-001")
        assert classified.delta is not None
        self.assertEqual(31, classified.delta.pr)
        self.assertEqual("compile", classified.delta.family)
        self.assertEqual("linux-x64", classified.delta.platform)
        self.assertEqual("a37ca145", classified.delta.base8)

    def test_non_suffixed_key_still_classifies_identically(self) -> None:
        # The optional group must not change parsing for every EXISTING
        # (pre-round-4C) delta key shape.
        entry = CacheEntry(
            id=2,
            ref="refs/pull/7/merge",
            key="delta-v1-pr7-compile-linux-x64-b43cef837",
            version="v1",
            size_in_bytes=1000,
            created_at="2026-09-29T02:49:27Z",
            last_accessed_at="2026-09-29T02:49:27Z",
        )
        (classified,) = classify(self.ci, [entry])
        self.assertTrue(classified.is_delta)
        assert classified.delta is not None
        self.assertEqual("43cef837", classified.delta.base8)


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


def _runs_jobs_fetch(runs_name: str, jobs_by_run_id: dict[int, str]):
    """A fake fetch answering `actions/runs?...` with `runs_name`'s fixture
    and `actions/runs/{id}/jobs` with `jobs_by_run_id[id]`'s fixture --
    RUST-004's own two-call shape (`_find_default_branch_dylint_run`).
    Never answers `actions/caches` -- these tests build `classified`
    directly (frozen `ClassifiedEntry`s), no cache listing involved."""

    def fetch(url: str, token: str):
        assert token
        if "/jobs" in url:
            for run_id, name in jobs_by_run_id.items():
                if f"/actions/runs/{run_id}/jobs" in url:
                    return _load(name)
            raise AssertionError(f"no jobs fixture registered for {url}")
        if "actions/runs" in url:
            return _load(runs_name)
        raise AssertionError(f"unexpected url in RUST-004 test: {url}")

    return fetch


def _classified_entry(entry_id: int, family_id: str, ref: str, created_at: str) -> ClassifiedEntry:
    entry = CacheEntry(
        id=entry_id,
        ref=ref,
        key=f"{family_id}-synthetic-{entry_id}",
        version="1",
        size_in_bytes=5_000_000,
        created_at=created_at,
        last_accessed_at=created_at,
    )
    return ClassifiedEntry(entry=entry, family_id=family_id, is_delta=False, delta=None, is_retired=False)


DYLINT_RUN_ID = 36509863041
MAIN_REF = "refs/heads/main"


class Rust004DylintOutputCacheSaveTest(unittest.TestCase):
    """Round-6E, item 3 (zackees/ci.yml#1): a 'setup-soldr:dylint-output'/
    'setup-soldr:dylint' family with NO live entry at all on the default
    branch, while the default branch's latest run has a successful
    'dylint' job -> RUST-004. `ci_lint/tests/fixtures/runtime/cache/repo/
    ci.toml` already declares both families ('dylint' via=setup-soldr:
    dylint, 'dylint-out' via=setup-soldr:dylint-output).

    NOT "no entry created since the job started": a live run against the
    real template repo (round-6E) proved that stricter signal false-
    positives on an entirely healthy case -- an unrelated commit can
    legitimately reproduce dylintOutputKey's IDENTICAL hash and correctly
    get an "exact hit - skipping save" (confirmed in zackees/
    template-python-rust-cmd run 36514507620's own job log), so an older
    entry is not itself evidence of anything wrong -- see
    `test_stale_but_present_entry_is_green` below."""

    def setUp(self) -> None:
        ci, findings = load_ci_toml(REPO)
        assert ci is not None, findings
        self.ci = ci

    def test_no_entry_at_all_on_default_branch_is_rust_004(self) -> None:
        # Only a PR-ref entry exists for 'dylint-out'; 'dylint' has a
        # fresh main-ref entry -- RUST-004 must fire for 'dylint-out'
        # ONLY, proving this is evaluated per family, not as one blanket
        # pass/fail.
        classified = (
            _classified_entry(1, "dylint-out", "refs/pull/9/merge", "2026-09-29T01:52:10Z"),
            _classified_entry(2, "dylint", MAIN_REF, "2026-09-29T01:52:10Z"),
        )
        fetch = _runs_jobs_fetch(
            "rust004-runs-main-success.json", {DYLINT_RUN_ID: "rust004-jobs-dylint-success.json"}
        )
        report = audit_classified(
            self.ci, classified, graphql=None, token=TOKEN, repo=REPO_SLUG, default_branch="main", fetch=fetch
        )
        rust004 = [f for f in report.findings if f.rule == "RUST-004"]
        self.assertEqual(1, len(rust004))
        self.assertIn("dylint-out", rust004[0].path)
        self.assertIn("no live entry at all", rust004[0].message)
        self.assertIn("setup-soldr#538", rust004[0].message)

    def test_stale_but_present_entry_is_green(self) -> None:
        # A main-ref entry exists for BOTH families, but 'dylint-out's is
        # from well before the qualifying job started -- an "exact hit,
        # skipping save" run (a legitimate, healthy warm cache, per the
        # class docstring's live evidence) looks EXACTLY like this. An
        # older-but-present entry is never itself a RUST-004 violation.
        classified = (
            _classified_entry(1, "dylint-out", MAIN_REF, "2026-09-20T00:00:00Z"),  # old, still present
            _classified_entry(2, "dylint", MAIN_REF, "2026-09-29T01:52:10Z"),
        )
        fetch = _runs_jobs_fetch(
            "rust004-runs-main-success.json", {DYLINT_RUN_ID: "rust004-jobs-dylint-success.json"}
        )
        report = audit_classified(
            self.ci, classified, graphql=None, token=TOKEN, repo=REPO_SLUG, default_branch="main", fetch=fetch
        )
        self.assertEqual([], [f for f in report.findings if f.rule == "RUST-004"])

    def test_entry_present_on_default_branch_is_green(self) -> None:
        classified = (
            _classified_entry(1, "dylint-out", MAIN_REF, "2026-09-29T01:52:10Z"),
            _classified_entry(2, "dylint", MAIN_REF, "2026-09-29T01:52:10Z"),
        )
        fetch = _runs_jobs_fetch(
            "rust004-runs-main-success.json", {DYLINT_RUN_ID: "rust004-jobs-dylint-success.json"}
        )
        report = audit_classified(
            self.ci, classified, graphql=None, token=TOKEN, repo=REPO_SLUG, default_branch="main", fetch=fetch
        )
        self.assertEqual([], [f for f in report.findings if f.rule == "RUST-004"])

    def test_no_successful_dylint_job_yet_is_silent_not_a_false_positive(self) -> None:
        classified = ()  # no live cache entries at all
        fetch = _runs_jobs_fetch(
            "rust004-runs-main-success.json", {DYLINT_RUN_ID: "rust004-jobs-dylint-failure.json"}
        )
        report = audit_classified(
            self.ci, classified, graphql=None, token=TOKEN, repo=REPO_SLUG, default_branch="main", fetch=fetch
        )
        self.assertEqual([], [f for f in report.findings if f.rule == "RUST-004"])

    def test_no_fetch_given_skips_rust_004_entirely(self) -> None:
        """`fetch=None` (the default, and what ci_lint.cache.ops's
        trim/janitor/preprune pass implicitly by never naming it) means
        zero RUST-004 network calls -- identical to pre-round-6E
        behavior."""

        classified = (_classified_entry(1, "dylint-out", "refs/pull/9/merge", "2026-09-29T01:52:10Z"),)
        report = audit_classified(
            self.ci, classified, graphql=None, token=TOKEN, repo=REPO_SLUG, default_branch="main"
        )
        self.assertEqual([], [f for f in report.findings if f.rule == "RUST-004"])

    def test_no_declared_dylint_family_never_calls_fetch(self) -> None:
        ci, findings = load_ci_toml(FIXTURES / "runtime" / "suite")
        assert ci is not None, findings
        self.assertFalse(any(fam.via in ("setup-soldr:dylint", "setup-soldr:dylint-output") for fam in ci.cache.family.values()))

        def unreachable_fetch(url: str, token: str):
            raise AssertionError("fetch must not be called when no dylint family is declared")

        report = audit_classified(
            ci, (), graphql=None, token=TOKEN, repo=REPO_SLUG, default_branch="main", fetch=unreachable_fetch
        )
        self.assertEqual([], [f for f in report.findings if f.rule == "RUST-004"])


class SetupUv64HexGenerationTest(unittest.TestCase):
    """zackees/ci.yml#104: setup-uv's own trailing key hash is a full
    sha256 (64 hex chars), longer than the 6-40 hex bound `_shape` used to
    strip. A superseded setup-uv-2- generation for the SAME platform stem
    must still be detected (RED before the fix, GREEN after); a different
    platform stem with the same hash shape must never be grouped with it,
    and setup-soldr's cargo-registry toolchain-digest handling (#101) must
    stay unaffected since it matches its own regex before the trailing-hash
    strip ever runs."""

    def setUp(self) -> None:
        ci, findings = load_ci_toml(REPO)
        assert ci is not None, findings
        self.ci = ci
        entries = list_caches(_fetch_from(_load("caches-setup-uv-64hex-generation.json")), TOKEN, REPO_SLUG)
        self.classified = classify(ci, entries)
        self.by_id = {c.entry.id: c for c in self.classified}

    def test_all_three_classify_as_setup_uv_cache(self) -> None:
        self.assertEqual("setup-uv-cache", self.by_id[1].family_id)
        self.assertEqual("setup-uv-cache", self.by_id[2].family_id)
        self.assertEqual("setup-uv-cache", self.by_id[3].family_id)

    def test_older_same_platform_generation_is_superseded(self) -> None:
        report = audit_classified(
            self.ci, self.classified, graphql=None, token=TOKEN, repo=REPO_SLUG, default_branch="main"
        )
        c006 = [f for f in report.findings if f.rule == "CACHE-006"]
        self.assertEqual(1, len(c006), [f.message for f in c006])
        self.assertTrue(c006[0].message.startswith("cache id=1 "), c006[0].message)
        self.assertIn("kept newest id=2 ", c006[0].message)
        # id 3 (different platform) never appears as superseded or as "kept".
        self.assertNotIn("id=3", c006[0].message)


if __name__ == "__main__":
    unittest.main()


class SetupUvLegacyGenerationTest(unittest.TestCase):
    """zackees/ci.yml#87: astral-sh/setup-uv v6 writes `setup-uv-1-` keys;
    a declared `via = "setup-uv"` family must own them (GREEN) while an
    unknown generation still reports CACHE-001 (RED)."""

    def setUp(self) -> None:
        ci, findings = load_ci_toml(REPO)
        assert ci is not None, findings
        self.ci = ci
        entries = list_caches(_fetch_from(_load("caches-setup-uv-v1.json")), TOKEN, REPO_SLUG)
        self.classified = classify(ci, entries)
        self.by_id = {c.entry.id: c for c in self.classified}

    def test_green_v1_and_v2_generations_classify(self) -> None:
        self.assertEqual("setup-uv-cache", self.by_id[1].family_id)
        self.assertEqual("setup-uv-cache", self.by_id[2].family_id)

    def test_red_unknown_generation_stays_cache_001(self) -> None:
        self.assertIsNone(self.by_id[3].family_id)
        report = audit_classified(
            self.ci, self.classified, graphql=None, token=TOKEN, repo=REPO_SLUG, default_branch="main"
        )
        c001 = [f for f in report.findings if f.rule == "CACHE-001"]
        self.assertEqual(1, len(c001))
        self.assertIn("id=3 ", c001[0].message)


class RegistryPerJobDigestTest(unittest.TestCase):
    """zackees/ci.yml#88: setup-soldr cargo-registry keys end in
    `<Cargo.lock hash>-<toolchain digest>`; per-job digests are concurrent
    live entries (GREEN), only an older Cargo.lock hash under the SAME digest
    is superseded (RED, CACHE-006)."""

    def setUp(self) -> None:
        ci, findings = load_ci_toml(REPO)
        assert ci is not None, findings
        self.ci = ci
        entries = list_caches(_fetch_from(_load("caches-registry-per-job.json")), TOKEN, REPO_SLUG)
        self.classified = classify(ci, entries)

    def test_only_the_older_lockfile_generation_is_superseded(self) -> None:
        self.assertTrue(all(c.family_id == "registry" for c in self.classified))
        report = audit_classified(
            self.ci, self.classified, graphql=None, token=TOKEN, repo=REPO_SLUG, default_branch="main"
        )
        c006 = [f for f in report.findings if f.rule == "CACHE-006"]
        self.assertEqual(1, len(c006), [f.message for f in c006])
        self.assertTrue(c006[0].message.startswith("cache id=4 "), c006[0].message)
        self.assertIn("kept newest id=1 ", c006[0].message)
