"""`ci-lint cache audit`: live, read-only classification of every cache
entry the GitHub Actions cache API reports, against ci.toml's declared
families (round-4A brief, deliverable 4). Round-6E adds RUST-004: a
`setup-soldr:dylint-output`/`setup-soldr:dylint` family with NO live entry
at all on the default branch, while the default branch's latest run has a
successful `dylint` job -- "a successful Dylint run never saved its output
cache" (setup-soldr#538/#540, zackees/ci.yml#1).

RUST-004 cannot literally recompute setup-soldr's own cache-key hash to
match "the current Cargo.lock hash" the way a `via = "ci-lint"` family's
key can be rebuilt (`ci_lint.cache.keys`): `dylintOutputKey`'s hash covers
`cargo_lock` *together with* `source_revision` (the exact commit SHA),
`target_shape`, and several other inputs ci-lint has no access to
(setup-soldr `src/lib/resolve-setup.ts`, read read-only, ~line 1185); it is
fundamentally source-SHA-keyed, not a reproducible function of Cargo.lock
content alone. An earlier design correlated a family's live entries'
`created_at` against the qualifying job's own `started_at` instead --
proven WRONG live against the real template repo (round-6E): an unrelated
commit (workflow/docs-only) legitimately reproduces the IDENTICAL
`dylintOutputHash` and gets a correct "exact hit - skipping save" (the
existing entry is still perfectly valid; nothing was silently lost), so
"no entry created since this job started" false-positives on ordinary warm
reuse. The only signal ci-lint can trust without literally decoding the
hash is coarser but sound: does ANY live entry for the family exist on the
default branch at all -- exactly zackees/clud's observed failure mode
(zero cache layers reused, ever, on either sampled run). See
`_find_default_branch_dylint_run`/`_check_rust_004` below.

Requires `GITHUB_TOKEN` + `GITHUB_REPOSITORY` and `actions: read` (listing
caches, and -- when `fetch` is given to `audit_classified`/`run_audit` --
RUST-004's runs/jobs lookup); the PR-state half of CACHE-008 additionally
needs a `GraphQLFn` (also just a read). Never deletes anything -- see
`ci_lint.cache.ops` for trim/janitor/heal/preprune, which reuse this
module's classification and then act on it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from ci_lint.cache.families import resolve_prefix, resolve_retired_prefix
from ci_lint.cargo_messages import JsonValue
from ci_lint.cache.github_cache import (
    CacheApiError,
    CacheEntry,
    PrState,
    fetch_pr_states,
    list_caches,
)
from ci_lint.finding import Finding
from ci_lint.github_api import FetchFn, GitHubApiError, GraphQLFn
from ci_lint.rules.cache_static import parse_size
from ci_lint.schema import CiToml

# RUST-004: the two `via` values whose live entries this correlates against
# real Dylint run evidence -- the output-cache family (source-SHA-keyed,
# issue #1's "cold on every new commit" finding) and the driver/foundation
# family (issue #1's qualified-vs-short-nightly identity-bridge gap).
DYLINT_CACHE_VIA_VALUES: frozenset[str] = frozenset({"setup-soldr:dylint-output", "setup-soldr:dylint"})
_RUST_004_RUNS_PER_PAGE = 20
_RUST_004_JOBS_PER_PAGE = 50

# CACHE-005: "poisoned/tiny (< family min, or <= 1KB for any family)" --
# the 1KB floor applies regardless of whether a family declares its own
# `min` (issue #6 §6: this is exactly clud's 253-byte entry).
TINY_BYTES = 1024
BUDGET_WARN_RATIO = 0.90

# `delta-v1-pr<N>-<family>-<platform>-b<base8>[-g<gen8>]` -- family AND
# platform ids both legitimately contain hyphens (e.g. "linux-x64"), so a
# single blind regex with a `[^-]+` platform group mis-splits them
# (round-4A: caught by ci_lint/tests/test_cache_audit.py against a
# synthetic "compile"/"linux-x64" delta key, which a naive regex parsed as
# family="compile-linux", platform="x64"). Only the PR number and the
# base8 suffix are unambiguous from the string alone; `_split_family_
# platform` below resolves the rest against ci.toml's own declared
# platform ids. The trailing `-g<gen8>` is round-4C's per-generation
# commit-sha suffix (docs/ci-toml.md, template-python-rust-cmd#31): cache
# entries are immutable, so each save needs a distinct key, and the
# previous generation is healed (deleted) once a new one lands -- but
# CLOSED/MERGED-PR trim (CACHE-008) must still recognize a generation-
# suffixed key as a delta at all (round-4A's classifier predates the
# generation suffix and silently misclassified it as CACHE-001
# "undeclared family" instead, so a merged PR's straggler generation
# was never trimmed -- see the round-4C worker report). `gen8` is parsed
# but not otherwise used by classification: the delta's identity for
# staleness/closed-PR purposes is still (pr, family, platform, base8).
DELTA_OUTER_RE = re.compile(
    r"^delta-v1-pr(?P<pr>\d+)-(?P<rest>.+)-b(?P<base8>[0-9a-f]{8})(?:-g(?P<gen8>[0-9a-f]{6,40}))?$"
)

# CACHE-006 "superseded": two entries of the same declared family whose
# keys differ only in a trailing lockfile/version hash. Best-effort: strip
# one or more trailing hyphen-hex segments (6-40 hex chars each -- covers
# both the 16-hex short hashes and 8-hex base tokens observed live) and
# group by what's left. A platform/os component earlier in the key (never
# hex-only, e.g. "linux-x64") is never stripped, so per-platform families
# are never treated as superseding each other.
_TRAILING_HASH_RE = re.compile(r"(?:-[0-9a-f]{6,40})+$")


def _shape(key: str) -> str:
    stripped = _TRAILING_HASH_RE.sub("", key)
    return stripped if stripped else key


@dataclass(frozen=True)
class DeltaIdentity:
    pr: int
    family: str
    platform: str
    base8: str


@dataclass(frozen=True)
class ClassifiedEntry:
    entry: CacheEntry
    family_id: str | None  # a declared [cache.family.<id>] id, or None
    is_delta: bool
    delta: DeltaIdentity | None
    is_retired: bool  # matches a [cache].retired prefix


@dataclass(frozen=True)
class AuditReport:
    classified: tuple[ClassifiedEntry, ...]
    findings: tuple[Finding, ...]
    total_bytes: int
    budget_bytes: int | None
    budget_ratio: float | None
    warning: str | None = None


def _split_family_platform(rest: str, ci: CiToml) -> tuple[str, str] | None:
    """`rest` is `"<family>-<platform>"`. Resolve it against ci.toml's own
    declared platform ids (longest first, though ids are expected unique)
    rather than guessing with a hyphen-free regex group."""

    for platform_id in sorted(ci.platforms, key=len, reverse=True):
        suffix = f"-{platform_id}"
        if rest.endswith(suffix) and len(rest) > len(suffix):
            return rest[: -len(suffix)], platform_id
    return None


def classify(ci: CiToml, entries: list[CacheEntry]) -> tuple[ClassifiedEntry, ...]:
    return tuple(_classify_one(ci, e) for e in entries)


def _classify_one(ci: CiToml, entry: CacheEntry) -> ClassifiedEntry:
    m = DELTA_OUTER_RE.match(entry.key)
    if m is not None:
        split = _split_family_platform(m.group("rest"), ci)
        if split is not None:
            family, platform = split
            delta = DeltaIdentity(pr=int(m.group("pr")), family=family, platform=platform, base8=m.group("base8"))
            return ClassifiedEntry(
                entry=entry,
                family_id=family if family in ci.cache.family else None,
                is_delta=True,
                delta=delta,
                is_retired=False,
            )
        # A delta-shaped key whose platform segment matches no declared
        # platform id: fall through to the undeclared-family path below
        # (never silently misclassified as a base layer either).

    for retired_name in ci.cache.retired:
        if entry.key.startswith(resolve_retired_prefix(retired_name)):
            return ClassifiedEntry(entry=entry, family_id=None, is_delta=False, delta=None, is_retired=True)

    for fam_id, fam in ci.cache.family.items():
        prefix = resolve_prefix(fam.via, fam_id)
        if prefix is not None and entry.key.startswith(prefix):
            return ClassifiedEntry(entry=entry, family_id=fam_id, is_delta=False, delta=None, is_retired=False)

    return ClassifiedEntry(entry=entry, family_id=None, is_delta=False, delta=None, is_retired=False)


def _check_cache_001(classified: tuple[ClassifiedEntry, ...]) -> list[Finding]:
    findings: list[Finding] = []
    for c in classified:
        if c.is_retired or c.family_id is not None:
            continue
        kind = "PR delta for an" if c.is_delta else "cache"
        findings.append(
            Finding(
                rule="CACHE-001",
                path=f"cache:{c.entry.key}",
                message=f"{kind} undeclared family: id={c.entry.id} key {c.entry.key!r} matches no "
                f"declared [cache.family] prefix",
                fix="declare a [cache.family.<id>] entry whose 'via' resolves to this key's prefix "
                "(see docs/ci-toml.md's family table), or stop writing it -- ci-lint cache janitor "
                "deletes an undeclared entry once nothing produces it any more",
            )
        )
    return findings


def _check_cache_003(
    classified: tuple[ClassifiedEntry, ...], default_branch: str
) -> list[Finding]:
    default_ref = f"refs/heads/{default_branch}"
    findings: list[Finding] = []
    for c in classified:
        if c.family_id is None or c.is_delta or c.is_retired:
            continue
        if c.entry.ref and c.entry.ref != default_ref:
            findings.append(
                Finding(
                    rule="CACHE-003",
                    path=f"cache:{c.entry.key}",
                    message=f"base-layer family '{c.family_id}' cache id={c.entry.id} was saved on "
                    f"ref {c.entry.ref!r}, not the default branch ({default_ref!r})",
                    fix="base cache layers may only be written by a declared writer flow on the "
                    "default branch (issue #6 §6, [cache].write-on) -- delete this entry (ci-lint "
                    "cache janitor) and check the writer job's ref condition / save-ok call site",
                )
            )
    return findings


def _check_cache_005(ci: CiToml, classified: tuple[ClassifiedEntry, ...]) -> list[Finding]:
    findings: list[Finding] = []
    for c in classified:
        entry = c.entry
        reason: str | None = None
        if entry.size_in_bytes <= TINY_BYTES:
            reason = f"{entry.size_in_bytes}B is at or below the {TINY_BYTES}B poison-guard floor"
        elif c.family_id is not None:
            fam = ci.cache.family[c.family_id]
            if fam.min:
                min_bytes = parse_size(fam.min)
                if min_bytes is not None and entry.size_in_bytes < min_bytes:
                    reason = f"{entry.size_in_bytes}B is below [cache.family.{c.family_id}].min ({fam.min})"
        if reason is not None:
            findings.append(
                Finding(
                    rule="CACHE-005",
                    path=f"cache:{entry.key}",
                    message=f"cache id={entry.id} key {entry.key!r} looks poisoned: {reason}",
                    fix="delete the exact key (ci-lint cache heal --key <key>) so the next writer run "
                    "repopulates it with a real payload",
                )
            )
    return findings


def _check_cache_006(classified: tuple[ClassifiedEntry, ...]) -> list[Finding]:
    groups: dict[tuple[str, str], list[ClassifiedEntry]] = {}
    for c in classified:
        if c.family_id is None or c.is_delta or c.is_retired:
            continue
        groups.setdefault((c.family_id, _shape(c.entry.key)), []).append(c)

    findings: list[Finding] = []
    for (fam_id, _shape_key), members in groups.items():
        if len(members) < 2:
            continue
        newest = max(members, key=lambda c: c.entry.last_accessed_at)
        for c in members:
            if c.entry.id == newest.entry.id:
                continue
            findings.append(
                Finding(
                    rule="CACHE-006",
                    path=f"cache:{c.entry.key}",
                    message=f"cache id={c.entry.id} is a superseded '{fam_id}' entry "
                    f"(last_accessed_at={c.entry.last_accessed_at!r}; kept newest "
                    f"id={newest.entry.id} last_accessed_at={newest.entry.last_accessed_at!r})",
                    fix="delete the superseded entry (ci-lint cache janitor); if this family "
                    "legitimately needs more than one live entry at once, disambiguate it with "
                    "[cache.family.<id>].per instead of relying on two same-shape keys",
                )
            )
    return findings


def _check_cache_009(classified: tuple[ClassifiedEntry, ...]) -> list[Finding]:
    findings: list[Finding] = []
    for c in classified:
        if c.is_retired:
            findings.append(
                Finding(
                    rule="CACHE-009",
                    path=f"cache:{c.entry.key}",
                    message=f"cache id={c.entry.id} key {c.entry.key!r} matches a retired family "
                    "([cache].retired)",
                    fix="delete it (ci-lint cache janitor); a retired family must never be written -- "
                    "check the writer flow's setup-soldr wrapper inputs for what is still producing it",
                )
            )
    return findings


def _as_dict(value: JsonValue) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def _as_list(value: JsonValue) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def _is_dylint_job_name(name: str) -> bool:
    """The template's job id is literally `dylint`, but its DISPLAY name
    carries the lane-digest suffix `ci_lint.plan`'s title-edit reuse relies
    on (e.g. `"dylint [309e89b739f0]"`) -- match the prefix, not equality."""

    return name == "dylint" or name.startswith("dylint ") or name.startswith("dylint[")


@dataclass(frozen=True)
class _DylintRunEvidence:
    run_id: int
    html_url: str


def _find_default_branch_dylint_run(
    fetch: FetchFn, token: str, repo: str, default_branch: str
) -> _DylintRunEvidence | None:
    """The default branch's most recent run (event=push, its own top-level
    conclusion=success) that has a successful `dylint`-named job --
    `None` when no such run/job is found, so RUST-004 has nothing to
    correlate a missing cache entry against yet (never a false positive on
    a repo/branch that has simply never run Dylint successfully). Matches
    `ci_lint.reuse.compute_reuse`'s "no network in tests, inject FetchFn"
    convention; raises `GitHubApiError` on a transport/HTTP failure, same
    as every other live call in this module."""

    payload = fetch(
        f"https://api.github.com/repos/{repo}/actions/runs"
        f"?branch={default_branch}&event=push&status=success&per_page={_RUST_004_RUNS_PER_PAGE}",
        token,
    )
    runs = _as_list(_as_dict(payload).get("workflow_runs"))
    for raw in runs:
        run = _as_dict(raw)
        run_id = run.get("id")
        html_url = run.get("html_url")
        if not isinstance(run_id, int):
            continue
        jobs_payload = fetch(
            f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
            f"?per_page={_RUST_004_JOBS_PER_PAGE}",
            token,
        )
        for raw_job in _as_list(_as_dict(jobs_payload).get("jobs")):
            job = _as_dict(raw_job)
            name = job.get("name")
            conclusion = job.get("conclusion")
            if not isinstance(name, str) or not _is_dylint_job_name(name):
                continue
            if conclusion != "success":
                continue
            return _DylintRunEvidence(run_id=run_id, html_url=html_url if isinstance(html_url, str) else "")
    return None


def _check_rust_004(
    ci: CiToml,
    classified: tuple[ClassifiedEntry, ...],
    *,
    fetch: FetchFn,
    token: str,
    repo: str,
    default_branch: str,
) -> tuple[list[Finding], str | None]:
    families = sorted(fam_id for fam_id, fam in ci.cache.family.items() if fam.via in DYLINT_CACHE_VIA_VALUES)
    if not families:
        return [], None

    try:
        run = _find_default_branch_dylint_run(fetch, token, repo, default_branch)
    except GitHubApiError as exc:
        return [], f"RUST-004: could not confirm the default branch's latest successful dylint job: {exc}"
    if run is None:
        return [], None

    default_ref = f"refs/heads/{default_branch}"
    findings: list[Finding] = []
    for fam_id in families:
        on_default = [
            c
            for c in classified
            if c.family_id == fam_id and not c.is_delta and not c.is_retired and c.entry.ref == default_ref
        ]
        # NOT "created_at >= the job's own started_at": a live run against
        # the real template (round-6E) proved that signal false-positives
        # on an entirely legitimate case -- setup-soldr's dylint-output key
        # is a hash over (among other things) the workspace's manifest/
        # target-shape/compiler identity, NOT the commit SHA alone, so an
        # unrelated commit (e.g. a docs/workflow-only change) reproduces the
        # IDENTICAL key and correctly gets an "exact hit - skipping save"
        # (confirmed in zackees/template-python-rust-cmd run 36514507620's
        # own job log) -- a perfectly healthy warm cache, not a missed
        # save. The only signal ci-lint can trust without literally
        # recomputing that hash is "does ANY live entry for this family
        # exist on the default branch at all" -- exactly the failure mode
        # issue #1 actually observed (zackees/clud: zero cache layers
        # reused on either sampled run).
        if on_default:
            continue
        via = ci.cache.family[fam_id].via
        run_ref = run.html_url or f"run id={run.run_id}"
        findings.append(
            Finding(
                rule="RUST-004",
                path=f"cache:family:{fam_id}",
                message=f"successful Dylint run never saved its output cache (see setup-soldr#538/#540): "
                f"{run_ref}'s 'dylint' job succeeded on {default_ref!r}, but family '{fam_id}' ({via}) "
                "has no live entry at all on the default branch",
                fix=f"inspect {run_ref}'s Dylint job log for its 'Setup soldr' Post-job step -- "
                "setup-soldr's save gate compares a QUALIFIED nightly identity (e.g. "
                "'nightly-2026-05-28-x86_64-unknown-linux-gnu') against a short one and can silently "
                "skip saving on a mismatch (setup-soldr#538, fixed in v0.9.81+); confirm setup-soldr is "
                f"pinned to >= v0.9.81 and that the job actually reports a cache save (not just a hit) "
                f"for '{fam_id}'",
            )
        )
    return findings, None


def _current_base_entries(classified: tuple[ClassifiedEntry, ...]) -> dict[str, list[ClassifiedEntry]]:
    out: dict[str, list[ClassifiedEntry]] = {}
    for c in classified:
        if c.family_id is not None and not c.is_delta and not c.is_retired:
            out.setdefault(c.family_id, []).append(c)
    return out


def _check_cache_008(
    classified: tuple[ClassifiedEntry, ...],
    *,
    graphql: GraphQLFn | None,
    token: str,
    repo: str,
) -> tuple[list[Finding], str | None]:
    deltas = [c for c in classified if c.is_delta and c.delta is not None]
    if not deltas:
        return [], None

    warning: str | None = None
    pr_states: dict[int, PrState] = {}
    if graphql is not None:
        pr_numbers = sorted({c.delta.pr for c in deltas if c.delta is not None})
        try:
            owner, name = repo.split("/", 1)
            pr_states = fetch_pr_states(graphql, token, owner, name, pr_numbers)
        except CacheApiError as exc:
            warning = f"CACHE-008 PR-state lookup: {exc}"

    base_entries = _current_base_entries(classified)
    findings: list[Finding] = []
    for c in deltas:
        d = c.delta
        assert d is not None
        state = pr_states.get(d.pr)
        if state is not None and state.closed_or_merged:
            findings.append(
                Finding(
                    rule="CACHE-008",
                    path=f"cache:{c.entry.key}",
                    message=f"cache id={c.entry.id} is a PR delta for #{d.pr}, which is "
                    f"{state.state.lower()}",
                    fix="delete it (ci-lint cache trim); the next precheck on this repo trims a "
                    "closed/merged PR's delta automatically",
                )
            )
            continue

        candidates = base_entries.get(d.family, [])
        matching = [b for b in candidates if d.platform in b.entry.key] or candidates
        if not matching:
            continue  # no live base entry to compare against: unknown, not reported
        current_base8 = {hashlib.sha256(b.entry.key.encode("utf-8")).hexdigest()[:8] for b in matching}
        if d.base8 not in current_base8:
            findings.append(
                Finding(
                    rule="CACHE-008",
                    path=f"cache:{c.entry.key}",
                    message=f"cache id={c.entry.id} is a PR #{d.pr} delta for family '{d.family}' "
                    f"whose base hash b{d.base8} matches none of the live base entries' current keys",
                    fix="delete it (ci-lint cache trim); the next PR push rebuilds the delta against "
                    "the current base and saves fresh",
                )
            )
    return findings, warning


def audit_classified(
    ci: CiToml,
    classified: tuple[ClassifiedEntry, ...],
    *,
    graphql: GraphQLFn | None,
    token: str,
    repo: str,
    default_branch: str = "main",
    fetch: FetchFn | None = None,
) -> AuditReport:
    """The pure-ish half: findings from an already-fetched cache listing.
    Split from `run_audit` so `ci_lint.cache.ops` can classify once and
    reuse it for both the findings and the deletions. `fetch` is optional
    and keyword-only, defaulting to `None` (RUST-004 skipped, no runs/jobs
    lookup, identical output to before round-6E) -- `ci_lint.cache.ops`'s
    trim/janitor/preprune classify+audit for their OWN rule subsets (none
    of which include RUST-004, which never deletes anything) and so never
    pass it, keeping their live-call count unchanged; `run_audit` (used by
    `ci-lint cache audit` and `precheck --live`) always has a `fetch` in
    hand already (it just used it to list caches) and passes it through."""

    findings: list[Finding] = []
    findings.extend(_check_cache_001(classified))
    findings.extend(_check_cache_003(classified, default_branch))
    findings.extend(_check_cache_005(ci, classified))
    findings.extend(_check_cache_006(classified))
    findings.extend(_check_cache_009(classified))
    cache008_findings, warning = _check_cache_008(classified, graphql=graphql, token=token, repo=repo)
    findings.extend(cache008_findings)

    if fetch is not None:
        rust004_findings, rust004_warning = _check_rust_004(
            ci, classified, fetch=fetch, token=token, repo=repo, default_branch=default_branch
        )
        findings.extend(rust004_findings)
        if rust004_warning and warning is None:
            warning = rust004_warning

    total_bytes = sum(c.entry.size_in_bytes for c in classified)
    budget_bytes = parse_size(ci.cache.budget) if ci.cache.budget else None
    ratio = (total_bytes / budget_bytes) if budget_bytes else None
    if ratio is not None and ratio >= BUDGET_WARN_RATIO:
        findings.append(
            Finding(
                rule="CACHE-004",
                path="cache:budget",
                message=f"live cache usage {total_bytes}B is {ratio:.0%} of [cache].budget "
                f"({ci.cache.budget})",
                fix="run ci-lint cache janitor to reclaim undeclared/superseded/stale entries, or "
                "raise [cache].budget (re-proving CACHE-004's static arithmetic still fits)",
            )
        )

    return AuditReport(
        classified=classified,
        findings=tuple(findings),
        total_bytes=total_bytes,
        budget_bytes=budget_bytes,
        budget_ratio=ratio,
        warning=warning,
    )


class AuditError(Exception):
    """Listing caches itself failed (network/auth) -- distinct from a
    finding, since there is nothing to classify at all. Callers turn this
    into a needs_review, never a crash."""


def run_audit(
    ci: CiToml,
    *,
    fetch: FetchFn,
    graphql: GraphQLFn | None,
    token: str,
    repo: str,
    default_branch: str = "main",
) -> AuditReport:
    try:
        entries = list_caches(fetch, token, repo)
    except CacheApiError as exc:
        raise AuditError(str(exc)) from exc
    classified = classify(ci, entries)
    return audit_classified(
        ci, classified, graphql=graphql, token=token, repo=repo, default_branch=default_branch, fetch=fetch
    )


def to_json_dict(report: AuditReport) -> dict[str, object]:
    return {
        "entries": [
            {
                "id": c.entry.id,
                "key": c.entry.key,
                "ref": c.entry.ref,
                "size_in_bytes": c.entry.size_in_bytes,
                "last_accessed_at": c.entry.last_accessed_at,
                "family": c.family_id,
                "is_delta": c.is_delta,
                "is_retired": c.is_retired,
            }
            for c in report.classified
        ],
        "findings": [
            {"rule": f.rule, "status": f.status.value, "path": f.path, "message": f.message, "fix": f.fix}
            for f in report.findings
        ],
        "total_bytes": report.total_bytes,
        "budget_bytes": report.budget_bytes,
        "budget_ratio": report.budget_ratio,
        "warning": report.warning,
    }


def render_text(report: AuditReport) -> str:
    lines: list[str] = []
    by_family: dict[str, tuple[int, int]] = {}
    for c in report.classified:
        label = c.family_id or ("delta" if c.is_delta else ("retired" if c.is_retired else "undeclared"))
        count, size = by_family.get(label, (0, 0))
        by_family[label] = (count + 1, size + c.entry.size_in_bytes)
    lines.append(f"ci-lint cache audit: {len(report.classified)} live cache entrie(s), {report.total_bytes}B total")
    lines.append(f"{'family':<24} {'count':>6} {'bytes':>12}")
    for label in sorted(by_family):
        count, size = by_family[label]
        lines.append(f"{label:<24} {count:>6} {size:>12}")
    if report.budget_bytes is not None:
        pct = f"{report.budget_ratio:.1%}" if report.budget_ratio is not None else "?"
        lines.append(f"budget: {report.total_bytes}B / {report.budget_bytes}B ({pct})")
    lines.append("")
    if not report.findings:
        lines.append("ci-lint cache audit: no findings.")
    for f in report.findings:
        lines.append(f.render())
    if report.warning:
        lines.append(f"warning: {report.warning}")
    return "\n".join(lines)
