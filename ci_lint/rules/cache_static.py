"""Group 9: static cache rules (CACHE-001, CACHE-002, CACHE-004).

No workflow may call `actions/cache` directly (setup-soldr owns cache
identity); no cache key may embed a per-run volatile value; and the total
declared cache footprint (steady state + one lockfile-change peak + the PR
delta budget) must fit under `[cache].budget`, which itself may not exceed
GitHub's 10GB default repository cap.
"""

from __future__ import annotations

import re
from pathlib import Path

from ci_lint.finding import Finding
from ci_lint.resolve import resolve_flow
from ci_lint.schema import CiToml
from ci_lint.workflow_scan import as_dict, load_composite_actions, load_workflows
from ci_lint.yaml_io import LoadStatus, YamlValue
from ci_lint.rules.tools import _iter_uses_with

SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(B|KB|MB|GB)$", re.IGNORECASE)
SIZE_UNITS: dict[str, int] = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
TEN_GB = 10 * 1024**3

VOLATILE_TOKENS: tuple[str, ...] = ("github.sha", "github.run_id", "github.run_number")
KEY_FIELD_NAMES: frozenset[str] = frozenset({"key", "cache-key-suffix"})


def parse_size(text: str) -> int | None:
    m = SIZE_RE.match(text.strip())
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2).upper()
    return int(value * SIZE_UNITS[unit])


def check_cache_001(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    files = [(w.path, False, as_dict(w.document)) for w in load_workflows(repo_root) if w.status == LoadStatus.OK]
    files += [
        (a.path, True, as_dict(a.document)) for a in load_composite_actions(repo_root) if a.status == LoadStatus.OK
    ]
    for path, is_composite, doc in files:
        for uses, loc, _with in _iter_uses_with(doc, is_composite=is_composite):
            slug = uses.split("@", 1)[0]
            if slug == "actions/cache" or slug.startswith("actions/cache/"):
                findings.append(
                    Finding(
                        rule="CACHE-001",
                        path=path,
                        message=f"{loc}: raw '{slug}' is used directly",
                        fix=f"remove {loc}; caching goes through zackees/setup-soldr's declared "
                        "families (or the ci-lint 'uv' family), never a raw actions/cache call",
                    )
                )
    return findings


def _check_key_value(value: YamlValue, path: str, loc: str) -> list[Finding]:
    if not isinstance(value, str):
        return []
    out: list[Finding] = []
    for token in VOLATILE_TOKENS:
        if token in value:
            out.append(
                Finding(
                    rule="CACHE-002",
                    path=path,
                    message=f"{loc} contains volatile component '{token}': {value!r}",
                    fix=f"remove '{token}' from {loc}; key on hashFiles(<lockfile>) or a date-based "
                    "rotation instead of a per-run value",
                )
            )
    return out


def check_cache_002(ci: CiToml, repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    files = [(w.path, False, as_dict(w.document)) for w in load_workflows(repo_root) if w.status == LoadStatus.OK]
    files += [
        (a.path, True, as_dict(a.document)) for a in load_composite_actions(repo_root) if a.status == LoadStatus.OK
    ]
    for path, is_composite, doc in files:
        for uses, loc, with_ in _iter_uses_with(doc, is_composite=is_composite):
            for field_name in KEY_FIELD_NAMES:
                findings.extend(_check_key_value(with_.get(field_name), path, f"{loc}.with.{field_name}"))
    for fam_id, fam in ci.cache.family.items():
        if fam.key is None:
            continue
        for component in fam.key:
            findings.extend(
                _check_key_value(component, "ci.toml", f"[cache.family.{fam_id}].key")
            )
    return findings


def _cardinality(ci: CiToml, per: str | None) -> int:
    """`per`'s cardinality multiplier. `ci_lint.schema` already rejects any
    value outside `CACHE_FAMILY_PER_VALUES` as `CT-002` (round-2A amendment
    2), so `None`/`"none"` are the only ways to reach the `1` fallback
    here legitimately; an invalid value that somehow arrives anyway still
    falls back to `1` rather than crashing -- its CT-002 finding is what
    tells the user it's wrong, not this arithmetic."""

    if per == "platform":
        return len(ci.platforms)
    if per == "cross-platform":
        return max(len(ci.platforms) - 1, 0)
    if per == "os":
        return len({p.group for p in ci.platforms.values()})
    return 1


def check_cache_004(ci: CiToml) -> tuple[list[Finding], str]:
    findings: list[Finding] = []
    budget = parse_size(ci.cache.budget)
    if budget is None:
        findings.append(
            Finding(
                rule="CACHE-004",
                path="ci.toml",
                message=f"[cache].budget {ci.cache.budget!r} is not a parseable size (e.g. '9GB')",
                fix="set [cache].budget to a number + unit, e.g. '9GB'",
            )
        )
        return findings, "CACHE-004: could not compute arithmetic -- [cache].budget is unparseable"

    if budget > TEN_GB:
        findings.append(
            Finding(
                rule="CACHE-004",
                path="ci.toml",
                message=f"[cache].budget = {ci.cache.budget} exceeds the 10GB repository cache cap",
                fix="lower [cache].budget to 10GB or less (GitHub's default per-repo cache cap)",
            )
        )

    lines: list[str] = []
    steady_total = 0
    lockfile_total = 0
    unparseable = False
    for fam_id in sorted(ci.cache.family):
        fam = ci.cache.family[fam_id]
        max_bytes = parse_size(fam.max)
        if max_bytes is None:
            findings.append(
                Finding(
                    rule="CACHE-004",
                    path="ci.toml",
                    message=f"[cache.family.{fam_id}].max {fam.max!r} is not a parseable size",
                    fix=f"set [cache.family.{fam_id}].max to a number + unit, e.g. '150MB'",
                )
            )
            unparseable = True
            continue
        card = _cardinality(ci, fam.per)
        steady = max_bytes * card
        steady_total += steady
        tag = " (lockfile peak counted again)" if fam.lockfile else ""
        lines.append(f"  {fam_id}: {fam.max} x {card} ({fam.per or 'none'}) = {steady} B{tag}")
        if fam.lockfile:
            lockfile_total += steady

    pr_budget = parse_size(ci.cache.pr.budget) if ci.cache.pr.budget else 0
    if ci.cache.pr.budget and pr_budget is None:
        findings.append(
            Finding(
                rule="CACHE-004",
                path="ci.toml",
                message=f"[cache.pr].budget {ci.cache.pr.budget!r} is not a parseable size",
                fix="set [cache.pr].budget to a number + unit, e.g. '1GB'",
            )
        )
        pr_budget = 0

    writer_flow_ids = [fid for fid, flow in ci.flows.items() if resolve_flow(ci, fid).cache == "write"]
    writer_flows_pre_pruned = [resolve_flow(ci, fid).pre_prune for fid in writer_flow_ids]
    all_pre_pruned = bool(writer_flow_ids) and all(writer_flows_pre_pruned)

    # `pre-prune = true` on every writer flow removes only the
    # lockfile-change-peak term (the moment old+new lockfile-keyed entries
    # briefly coexist is pruned before the write) -- it does NOT waive the
    # rest of the budget proof (round-2A brief, defect 4). Without full
    # pre-prune, the peak term still applies.
    if all_pre_pruned:
        worst = steady_total + pr_budget
        formula = "worst = steady + [cache.pr].budget (every writer flow pre-prunes: lockfile peak waived)"
    else:
        worst = steady_total + lockfile_total + pr_budget
        formula = "worst = steady + lockfile-change peak + [cache.pr].budget (not every writer flow pre-prunes)"

    arithmetic = (
        "CACHE-004 budget arithmetic:\n"
        + "\n".join(lines)
        + f"\n  steady total            = {steady_total} B"
        + f"\n  + lockfile-change peak  = {lockfile_total} B"
        + f"\n  + [cache.pr].budget     = {pr_budget} B"
        + f"\n  formula applied: {formula}"
        + f"\n  = worst case            = {worst} B"
        + f"\n  budget ([cache].budget) = {budget} B"
    )

    if unparseable:
        return findings, arithmetic

    if worst > budget:
        fix = (
            "lower family 'max' sizes or their cardinality ('per'), or raise [cache].budget (up to "
            "10GB)"
        )
        if not all_pre_pruned:
            fix += (
                "; setting 'pre-prune = true' on every writer flow (cache = \"write\") removes the "
                "lockfile-change-peak term, but the remaining steady total + [cache.pr].budget must "
                "still fit"
            )
        findings.append(
            Finding(
                rule="CACHE-004",
                path="ci.toml",
                message=f"cache budget exceeded: worst case {worst} B > budget {budget} B ({formula})",
                fix=fix,
            )
        )

    return findings, arithmetic


def check_group9(ci: CiToml, repo_root: Path) -> tuple[list[Finding], str]:
    findings: list[Finding] = []
    findings.extend(check_cache_001(repo_root))
    findings.extend(check_cache_002(ci, repo_root))
    cache_004_findings, arithmetic = check_cache_004(ci)
    findings.extend(cache_004_findings)
    return findings, arithmetic
