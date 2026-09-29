"""`ci-lint cache trim|janitor|heal|preprune`: live deletions built on
`ci_lint.cache.audit`'s classification (round-4A brief, deliverable 5).

Every delete needs `actions: write`. `--dry-run` (or no `DeleteFn` at all)
never calls `delete` -- the CLI layer always prints exactly what WOULD be
deleted in that case. Nothing here classifies an entry itself; it all reuses
`ci_lint.cache.audit`'s CACHE-001/003/005/006/008/009 findings and deletes
by cache id (or, for `heal`, by literal key -- no id lookup needed).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from ci_lint.cache.audit import ClassifiedEntry, audit_classified, classify
from ci_lint.cache.github_cache import CacheApiError, delete_cache_by_id, delete_cache_by_key, list_caches
from ci_lint.github_api import DeleteFn, FetchFn, GraphQLFn
from ci_lint.rules.cache_static import cardinality, parse_size
from ci_lint.schema import CiToml

JANITOR_RULES: frozenset[str] = frozenset({"CACHE-001", "CACHE-003", "CACHE-005", "CACHE-006", "CACHE-009"})
DEFAULT_STALE_DAYS = 5


@dataclass(frozen=True)
class DeletePlanEntry:
    id: int
    key: str
    size_in_bytes: int
    reason: str


@dataclass(frozen=True)
class OpsResult:
    planned: tuple[DeletePlanEntry, ...]  # every entry this run decided to delete (capped)
    deleted: tuple[DeletePlanEntry, ...]  # actually deleted (empty when dry_run)
    errors: tuple[str, ...]  # per-entry delete failures; never raised
    dry_run: bool
    warning: str | None = None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "planned": [p.__dict__ for p in self.planned],
            "deleted": [p.__dict__ for p in self.deleted],
            "errors": list(self.errors),
            "dry_run": self.dry_run,
            "warning": self.warning,
        }

    def render(self) -> str:
        lines = [f"ci-lint cache: {'would delete' if self.dry_run else 'deleted'} {len(self.planned)} entrie(s)"]
        for p in self.planned:
            lines.append(f"  id={p.id} key={p.key!r} {p.size_in_bytes}B -- {p.reason}")
        for e in self.errors:
            lines.append(f"  ERROR: {e}")
        if self.warning:
            lines.append(f"warning: {self.warning}")
        return "\n".join(lines)


def _apply(planned: list[DeletePlanEntry], *, delete: DeleteFn | None, token: str, repo: str, dry_run: bool) -> OpsResult:
    if dry_run or delete is None:
        return OpsResult(planned=tuple(planned), deleted=(), errors=(), dry_run=True)
    deleted: list[DeletePlanEntry] = []
    errors: list[str] = []
    for p in planned:
        try:
            delete_cache_by_id(delete, token, repo, p.id)
            deleted.append(p)
        except CacheApiError as exc:
            errors.append(f"id={p.id} key={p.key!r}: {exc}")
    return OpsResult(planned=tuple(planned), deleted=tuple(deleted), errors=tuple(errors), dry_run=False)


def _findings_to_plan(
    report_findings, by_key: dict[str, ClassifiedEntry], rules: frozenset[str] | None
) -> dict[str, DeletePlanEntry]:
    out: dict[str, DeletePlanEntry] = {}
    for f in report_findings:
        if rules is not None and f.rule not in rules:
            continue
        if not f.path or not f.path.startswith("cache:"):
            continue
        key = f.path[len("cache:") :]
        c = by_key.get(key)
        if c is None:
            continue
        out[key] = DeletePlanEntry(id=c.entry.id, key=c.entry.key, size_in_bytes=c.entry.size_in_bytes, reason=f"{f.rule}: {f.message}")
    return out


def trim(
    ci: CiToml,
    *,
    fetch: FetchFn,
    graphql: GraphQLFn | None,
    delete: DeleteFn | None,
    token: str,
    repo: str,
    max_deletes: int = 50,
    dry_run: bool = False,
    default_branch: str = "main",
) -> OpsResult:
    """Delete CACHE-008 entries only: closed/merged PR deltas (ONE
    GraphQL query for every referenced PR number, inside `audit_classified`)
    and stale-base deltas."""

    try:
        entries = list_caches(fetch, token, repo)
    except CacheApiError as exc:
        return OpsResult(planned=(), deleted=(), errors=(), dry_run=dry_run, warning=str(exc))
    classified = classify(ci, entries)
    report = audit_classified(ci, classified, graphql=graphql, token=token, repo=repo, default_branch=default_branch)
    by_key = {c.entry.key: c for c in classified}
    plan = _findings_to_plan(report.findings, by_key, frozenset({"CACHE-008"}))
    planned = list(plan.values())[:max_deletes]
    result = _apply(planned, delete=delete, token=token, repo=repo, dry_run=dry_run)
    if report.warning and result.warning is None:
        result = OpsResult(**{**result.__dict__, "warning": report.warning})
    return result


def _parse_iso8601(value: str) -> float | None:
    if not value:
        return None
    try:
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _family_table(classified: list[ClassifiedEntry]) -> dict[str, tuple[int, int]]:
    table: dict[str, tuple[int, int]] = {}
    for c in classified:
        label = c.family_id or ("delta" if c.is_delta else ("retired" if c.is_retired else "undeclared"))
        count, size = table.get(label, (0, 0))
        table[label] = (count + 1, size + c.entry.size_in_bytes)
    return table


def _before_after_table(before: list[ClassifiedEntry], deleted_ids: set[int]) -> str:
    after = [c for c in before if c.entry.id not in deleted_ids]
    before_table = _family_table(before)
    after_table = _family_table(after)
    lines = [f"{'family':<24} {'before(n)':>10} {'before(B)':>12} {'after(n)':>10} {'after(B)':>12}"]
    for label in sorted(set(before_table) | set(after_table)):
        bn, bb = before_table.get(label, (0, 0))
        an, ab = after_table.get(label, (0, 0))
        lines.append(f"{label:<24} {bn:>10} {bb:>12} {an:>10} {ab:>12}")
    return "\n".join(lines)


def janitor(
    ci: CiToml,
    *,
    fetch: FetchFn,
    graphql: GraphQLFn | None,
    delete: DeleteFn | None,
    token: str,
    repo: str,
    max_deletes: int = 100,
    dry_run: bool = False,
    stale_days: int = DEFAULT_STALE_DAYS,
    default_branch: str = "main",
    now: float | None = None,
) -> tuple[OpsResult, str]:
    """Delete CACHE-001/003/005/006/009 entries, plus any entry not
    accessed in `stale_days` days. Returns `(result, before/after table)`."""

    try:
        entries = list_caches(fetch, token, repo)
    except CacheApiError as exc:
        return OpsResult(planned=(), deleted=(), errors=(), dry_run=dry_run, warning=str(exc)), ""
    classified = classify(ci, entries)
    report = audit_classified(ci, classified, graphql=graphql, token=token, repo=repo, default_branch=default_branch)
    by_key = {c.entry.key: c for c in classified}
    plan = _findings_to_plan(report.findings, by_key, JANITOR_RULES)

    now_ts = now if now is not None else time.time()
    cutoff = now_ts - stale_days * 86400
    for c in classified:
        if c.entry.key in plan:
            continue
        ts = _parse_iso8601(c.entry.last_accessed_at)
        if ts is not None and ts < cutoff:
            plan[c.entry.key] = DeletePlanEntry(
                id=c.entry.id,
                key=c.entry.key,
                size_in_bytes=c.entry.size_in_bytes,
                reason=f"stale: not accessed in >= {stale_days} day(s) (last_accessed_at={c.entry.last_accessed_at})",
            )

    planned = list(plan.values())[:max_deletes]
    result = _apply(planned, delete=delete, token=token, repo=repo, dry_run=dry_run)
    deleted_or_planned_ids = {p.id for p in (result.deleted if not result.dry_run else result.planned)}
    table = _before_after_table(list(classified), deleted_or_planned_ids)
    if report.warning:
        result = OpsResult(**{**result.__dict__, "warning": report.warning})
    return result, table


def heal(*, delete: DeleteFn | None, token: str, repo: str, key: str, ref: str | None = None, dry_run: bool = False) -> OpsResult:
    """Delete exactly `key` -- no id lookup, no classification. A writer
    flow calls this right after restoring an unusable payload, so the
    save step that follows repopulates it (issue #6 §6's self-heal)."""

    planned = (DeletePlanEntry(id=-1, key=key, size_in_bytes=0, reason="explicit heal target"),)
    if dry_run or delete is None:
        return OpsResult(planned=planned, deleted=(), errors=(), dry_run=True)
    try:
        delete_cache_by_key(delete, token, repo, key, ref)
        return OpsResult(planned=planned, deleted=planned, errors=(), dry_run=False)
    except CacheApiError as exc:
        return OpsResult(planned=planned, deleted=(), errors=(str(exc),), dry_run=False)


def preprune(
    ci: CiToml,
    *,
    fetch: FetchFn,
    graphql: GraphQLFn | None,
    delete: DeleteFn | None,
    token: str,
    repo: str,
    lockfile_changed: bool,
    max_deletes: int = 50,
    dry_run: bool = False,
    default_branch: str = "main",
) -> tuple[OpsResult, str]:
    """The CACHE-004 arithmetic, using LIVE sizes where a family has a live
    base entry (else its declared `max`), forecasting whether writing a
    changed lockfile's new keys would push the account over budget. Over
    budget: delete the superseded (CACHE-006) lockfile-keyed entries of the
    families about to be written -- the old-lockfile-hash copies the new
    write is about to make redundant."""

    try:
        entries = list_caches(fetch, token, repo)
    except CacheApiError as exc:
        return OpsResult(planned=(), deleted=(), errors=(), dry_run=dry_run, warning=str(exc)), "preprune: could not list live caches"
    classified = classify(ci, entries)

    live_max: dict[str, int] = {}
    for c in classified:
        if c.family_id is not None and not c.is_delta and not c.is_retired:
            live_max[c.family_id] = max(live_max.get(c.family_id, 0), c.entry.size_in_bytes)

    lines: list[str] = []
    steady_total = 0
    lockfile_total = 0
    for fam_id in sorted(ci.cache.family):
        fam = ci.cache.family[fam_id]
        declared_max = parse_size(fam.max) or 0
        size = max(live_max.get(fam_id, 0), declared_max)
        card = cardinality(ci, fam.per)
        steady = size * card
        steady_total += steady
        if fam.lockfile:
            lockfile_total += steady
        source = "live" if fam_id in live_max and live_max[fam_id] >= declared_max else "declared"
        lines.append(f"  {fam_id}: {source} size={size}B x {card} = {steady}B" + (" (lockfile)" if fam.lockfile else ""))

    pr_budget = parse_size(ci.cache.pr.budget) if ci.cache.pr.budget else 0
    pr_budget = pr_budget or 0
    peak = lockfile_total if lockfile_changed else 0
    forecast = steady_total + peak + pr_budget
    budget_bytes = parse_size(ci.cache.budget) or 0

    summary = (
        "ci-lint cache preprune forecast (live sizes where known):\n"
        + "\n".join(lines)
        + f"\n  steady total          = {steady_total}B"
        + f"\n  + lockfile peak       = {peak}B (lockfile-changed={lockfile_changed})"
        + f"\n  + [cache.pr].budget   = {pr_budget}B"
        + f"\n  = forecast            = {forecast}B"
        + f"\n  budget ([cache].budget) = {budget_bytes}B"
    )

    if not lockfile_changed or forecast <= budget_bytes:
        return OpsResult(planned=(), deleted=(), errors=(), dry_run=dry_run), summary + "\n  under budget: nothing to preprune"

    lockfile_families = {fid for fid, fam in ci.cache.family.items() if fam.lockfile}
    report = audit_classified(ci, classified, graphql=graphql, token=token, repo=repo, default_branch=default_branch)
    by_key = {c.entry.key: c for c in classified}
    planned: list[DeletePlanEntry] = []
    for f in report.findings:
        if f.rule != "CACHE-006" or not f.path or not f.path.startswith("cache:"):
            continue
        c = by_key.get(f.path[len("cache:") :])
        if c is None or c.family_id not in lockfile_families:
            continue
        planned.append(
            DeletePlanEntry(id=c.entry.id, key=c.entry.key, size_in_bytes=c.entry.size_in_bytes, reason=f"preprune ({f.rule}): {f.message}")
        )
    planned = planned[:max_deletes]
    result = _apply(planned, delete=delete, token=token, repo=repo, dry_run=dry_run)
    return result, summary + f"\n  OVER budget: pruning {len(planned)} superseded lockfile-keyed entrie(s)"
