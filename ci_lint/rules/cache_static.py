"""Group 9: static cache rules (CACHE-001, CACHE-002, CACHE-003, CACHE-004).

`actions/cache` (and its `/save`/`/restore` sub-actions) may only appear
inside the one sanctioned wrapper directory `[allow].cache-actions.only-in`
(default `.github/actions/cache`, still SHA-pinned per SEC-004) -- anywhere
else is CACHE-001. Inside that wrapper, a `key:` input must itself be an
expression that reads a prior step's output or a passed-through input
(keys come from `ci_lint cache key`), never a literal -- CACHE-002.
CACHE-002 also still flags a volatile component (`github.sha`, ...) in any
`key:`/`cache-key-suffix:` anywhere. CACHE-003's static half:
`astral-sh/setup-uv`'s `save-cache` input, when `[allow].setup-uv.require`
says it must be `"plan"`-derived, must not be missing or a literal `"true"`
(round-4B; both are exactly the "saves on every PR push" failure mode
documented under "astral-sh/setup-uv's own cache" in docs/ci-toml.md).
Finally, the total declared cache footprint (steady state + one
lockfile-change peak + the PR delta budget) must fit under `[cache].budget`,
which itself may not exceed GitHub's 10GB default repository cap
(CACHE-004). CACHE-014 (M2-17, ci.yml#42) sizes `[cache.pr].max-per-pr`
against two independent signals, each optional so a repository that hasn't
supplied one gets no finding from that half: `[cache.pr].expected-open-prs`
(if set) times `max-per-pr` must not exceed `[cache.pr].budget` (a fleet of
open PRs whose deltas each hit the cap would blow the PR budget even
though every individual PR stayed under `max-per-pr`), and
`[cache.pr].measured-largest-delta` (if set, e.g. copied from a
`ci-lint cache delta manifest` run against a real large PR) must not
exceed `max-per-pr` (a cap sized below an already-observed delta silently
truncates that PR's saved cache every time).
"""

from __future__ import annotations

import re
from pathlib import Path

from ci_lint.finding import Finding, Status
from ci_lint.resolve import resolve_flow
from ci_lint.schema import CiToml
from ci_lint.workflow_scan import as_dict, load_composite_actions, load_workflows
from ci_lint.yaml_io import LoadStatus, YamlValue
from ci_lint.rules.tools import _iter_uses_with, _is_plan_expr

SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(B|KB|MB|GB)$", re.IGNORECASE)
SIZE_UNITS: dict[str, int] = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
TEN_GB = 10 * 1024**3
# CACHE-029: a family this large, with nothing bounding how many entries may
# exist at once, is what fills a repository's 10GB cache budget.
UNBOUNDED_FAMILY_BYTES = 256 * 1024**2

VOLATILE_TOKENS: tuple[str, ...] = ("github.sha", "github.run_id", "github.run_number")
KEY_FIELD_NAMES: frozenset[str] = frozenset({"key", "cache-key-suffix"})
CACHE_ACTION_SLUGS_PREFIX = "actions/cache"

# CACHE-010 (issue #7): env vars known to silently disable a cache layer
# while setup-soldr/zccache still reports the layer as configured --
# zackees/clud's `_dylint.yml` set ZCCACHE_DISABLE=1 (arrived incidentally
# in clud#487, a PR about Codex fallback instructions) alongside a declared
# zccache-unit/dylint-output family; the setup step's own
# `cache-policy layers=...` log line never changed, so nothing in a green
# run revealed the contradiction. Kept as an explicit allowlist per the
# issue's own open question ("should the kill-switch list be owned by
# setup-soldr as a machine-readable manifest") -- not yet true, so this
# starts as ci-lint's own reserved list, extend it as more are found.
KILL_SWITCH_ENV_VARS: frozenset[str] = frozenset(
    {"ZCCACHE_DISABLE", "SOLDR_NO_CACHE", "SOLDR_CACHE_DISABLE", "SOLDR_DYLINT_NO_CACHE"}
)
_TRUTHY_STRINGS: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def parse_size(text: str) -> int | None:
    m = SIZE_RE.match(text.strip())
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2).upper()
    return int(value * SIZE_UNITS[unit])


def _is_cache_action_slug(slug: str) -> bool:
    return slug == "actions/cache" or slug.startswith(f"{CACHE_ACTION_SLUGS_PREFIX}/")


def _loaded_files(repo_root: Path) -> list[tuple[str, bool, dict[str, YamlValue]]]:
    files = [(w.path, False, as_dict(w.document)) for w in load_workflows(repo_root) if w.status == LoadStatus.OK]
    files += [
        (a.path, True, as_dict(a.document)) for a in load_composite_actions(repo_root) if a.status == LoadStatus.OK
    ]
    return files


def check_cache_001(ci: CiToml, repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    only_in = ci.allow.cache_actions.only_in.strip("/")
    for path, is_composite, doc in _loaded_files(repo_root):
        file_dir = Path(path).parent.as_posix()
        for uses, loc, _with in _iter_uses_with(doc, is_composite=is_composite):
            slug = uses.split("@", 1)[0]
            if not _is_cache_action_slug(slug):
                continue
            if file_dir == only_in:
                continue  # the one sanctioned wrapper (still SHA-pinned per SEC-004)
            findings.append(
                Finding(
                    rule="CACHE-001",
                    path=path,
                    message=f"{loc}: raw '{slug}' is used directly outside the sanctioned wrapper "
                    f"'{only_in}' (found in '{file_dir}')",
                    fix=f"move this call into the composite-action wrapper at {only_in}/action.yml "
                    f"(SHA-pinned per SEC-004) and call that wrapper from {path} instead; caching "
                    "otherwise goes through zackees/setup-soldr's declared families (or the ci-lint "
                    "'uv' family), never a direct actions/cache call",
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


def _check_wrapper_key_is_dynamic(value: YamlValue, path: str, loc: str) -> list[Finding]:
    """Inside the sanctioned `actions/cache*` wrapper, `key:` must be an
    expression that reads a prior step's output or a passed-through input
    (round-4B: keys are built by `ci_lint cache key`, never hand-written) --
    a literal string, even one with no volatile token, is CACHE-002 here."""

    if not isinstance(value, str):
        return []
    if _is_plan_expr(value) or re.search(r"steps\.[\w-]+\.outputs\.", value):
        return []
    return [
        Finding(
            rule="CACHE-002",
            path=path,
            message=f"{loc} is a literal cache key inside the sanctioned wrapper: {value!r}",
            fix=f"build {loc} from 'ci_lint cache key' (e.g. a prior step's "
            "'${{ steps.<id>.outputs.key }}') or pass it through as '${{ inputs.key }}' -- never "
            "hardcode a literal key inside the wrapper",
        )
    ]


def check_cache_002(ci: CiToml, repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    only_in = ci.allow.cache_actions.only_in.strip("/")
    for path, is_composite, doc in _loaded_files(repo_root):
        file_dir = Path(path).parent.as_posix()
        for uses, loc, with_ in _iter_uses_with(doc, is_composite=is_composite):
            for field_name in KEY_FIELD_NAMES:
                findings.extend(_check_key_value(with_.get(field_name), path, f"{loc}.with.{field_name}"))
            slug = uses.split("@", 1)[0]
            if _is_cache_action_slug(slug) and file_dir == only_in and "key" in with_:
                findings.extend(
                    _check_wrapper_key_is_dynamic(with_.get("key"), path, f"{loc}.with.key")
                )
    for fam_id, fam in ci.cache.family.items():
        if fam.key is None:
            continue
        for component in fam.key:
            findings.extend(
                _check_key_value(component, "ci.toml", f"[cache.family.{fam_id}].key")
            )
    return findings


def _setup_uv_save_cache_violation(actual: YamlValue) -> str | None:
    """`None` if OK; else the reason `astral-sh/setup-uv`'s `save-cache`
    input fails the plan-driven requirement. Mirrors the two failure modes
    docs/ci-toml.md's "astral-sh/setup-uv's own cache" section documents as
    actually saving on every ordinary PR push: a missing input (falls back
    to the action's own `"auto"` default) and a literal `"true"` (forces a
    save regardless of ref). An explicit `"false"`, or any other value,
    is not this rule's concern."""

    if actual is None:
        return "is missing (falls back to the action's 'auto' default, which still saves on every PR push)"
    if isinstance(actual, str) and actual.strip().lower() == "true":
        return f"= {actual!r} is a literal 'true' (saves unconditionally, on every ref including PRs)"
    return None


def check_cache_003_setup_uv(ci: CiToml, repo_root: Path) -> list[Finding]:
    """Round-4B: every `astral-sh/setup-uv` use (no single wrapper location
    -- unlike setup-soldr, this action is meant to be called directly), for
    each `[allow].setup-uv.require` entry whose expected value is the
    special string `"plan"`."""

    findings: list[Finding] = []
    require = ci.allow.setup_uv.require
    if not require:
        return findings
    for path, is_composite, doc in _loaded_files(repo_root):
        for uses, loc, with_ in _iter_uses_with(doc, is_composite=is_composite):
            if uses.split("@", 1)[0] != "astral-sh/setup-uv":
                continue
            for key, expected in require.items():
                actual = with_.get(key)
                if expected == "plan":
                    reason = _setup_uv_save_cache_violation(actual)
                    if reason is not None:
                        findings.append(
                            Finding(
                                rule="CACHE-003",
                                path=path,
                                message=f"{loc}: astral-sh/setup-uv input '{key}' {reason}",
                                fix=f"set '{key}' to an expression derived from the precheck plan "
                                f"(e.g. '${{{{ needs.precheck.outputs.cache_save }}}}' in the caller, "
                                f"or '${{{{ inputs.{key} }}}}' inside a composite wrapper) at {loc}",
                            )
                        )
                elif actual is None or str(actual).lower() != expected.lower():
                    findings.append(
                        Finding(
                            rule="CACHE-003",
                            path=path,
                            message=f"{loc}: astral-sh/setup-uv input '{key}' = {actual!r}, expected "
                            f"'{expected}'",
                            fix=f'set \'{key}: "{expected}"\' at {loc}',
                        )
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


def cardinality(ci: CiToml, per: str | None) -> int:
    """Public alias of `_cardinality`, for callers outside this module
    (round-4A: `ci_lint.cache.ops.preprune` reuses the exact same `per`
    arithmetic CACHE-004 uses, with live sizes substituted where known --
    one implementation of the cardinality rule, not two)."""

    return _cardinality(ci, per)


def check_cache_004(ci: CiToml) -> tuple[list[Finding], str]:  # noqa: C901
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


def _is_truthy(value: YamlValue) -> bool:
    if not isinstance(value, (str, int, bool)):
        return False
    return str(value).strip().lower() in _TRUTHY_STRINGS


def _iter_env_entries(doc: dict[str, YamlValue]) -> list[tuple[str, YamlValue, str]]:
    """`(name, value, loc)` for every env var set at workflow, job, or step
    level -- a kill-switch is just as effective set at any of the three."""

    out: list[tuple[str, YamlValue, str]] = []
    top_env = doc.get("env")
    if isinstance(top_env, dict):
        out.extend((k, v, "env") for k, v in top_env.items())
    jobs = doc.get("jobs")
    if isinstance(jobs, dict):
        for job_id, job in jobs.items():
            if not isinstance(job, dict):
                continue
            job_env = job.get("env")
            if isinstance(job_env, dict):
                out.extend((k, v, f"jobs.{job_id}.env") for k, v in job_env.items())
            steps = job.get("steps")
            if isinstance(steps, list):
                for i, step in enumerate(steps):
                    if not isinstance(step, dict):
                        continue
                    step_env = step.get("env")
                    if isinstance(step_env, dict):
                        out.extend((k, v, f"jobs.{job_id}.steps[{i}].env") for k, v in step_env.items())
    return out


def check_cache_010(ci: CiToml, repo_root: Path) -> list[Finding]:
    """Issue #7: a workflow or job sets a cache-layer kill-switch env var
    while ci.toml still declares a [cache.family] backed by a matching
    setup-soldr/zccache layer -- the setup step's own summary keeps
    claiming the layer is active, so a green run never surfaces the
    contradiction on its own (see KILL_SWITCH_ENV_VARS above for the
    zackees/clud evidence this codifies)."""

    declared_active = any(
        fam.via.startswith("setup-soldr:") or fam.via.startswith("zccache") for fam in ci.cache.family.values()
    )
    if not declared_active:
        return []

    findings: list[Finding] = []
    for path, _is_composite, doc in _loaded_files(repo_root):
        for name, value, loc in _iter_env_entries(doc):
            if name in KILL_SWITCH_ENV_VARS and _is_truthy(value):
                findings.append(
                    Finding(
                        rule="CACHE-010",
                        path=path,
                        message=f"{loc}.{name} = {value!r} disables a cache layer while ci.toml declares "
                        "a [cache.family] via a matching setup-soldr/zccache backend",
                        fix=f"remove '{name}' (or set it falsy) at {loc} in {path} so the declared layer "
                        "is actually written; if it is intentionally disabled here, retire the matching "
                        "[cache.family] entry instead of leaving ci.toml claim it active",
                    )
                )
    return findings


def check_cache_014(ci: CiToml) -> list[Finding]:
    """M2-17 (ci.yml#42): warn when `[cache.pr].max-per-pr` is sized wrong
    for the repository's scale, using whichever of the two optional inputs
    is supplied. Both are warnings (not hard failures): unlike CACHE-004's
    proven worst-case arithmetic, these depend on inputs a repository may
    not have measured yet (an open-PR count estimate, a delta measurement)."""

    findings: list[Finding] = []
    max_per_pr = parse_size(ci.cache.pr.max_per_pr) if ci.cache.pr.max_per_pr else None
    if max_per_pr is None:
        return findings

    expected_open_prs = ci.cache.pr.expected_open_prs
    if expected_open_prs is not None:
        pr_budget = parse_size(ci.cache.pr.budget) if ci.cache.pr.budget else None
        if pr_budget is not None:
            fleet_worst = max_per_pr * expected_open_prs
            if fleet_worst > pr_budget:
                findings.append(
                    Finding(
                        rule="CACHE-014",
                        path="ci.toml",
                        message=(
                            f"[cache.pr].max-per-pr ({ci.cache.pr.max_per_pr} = {max_per_pr} B) x "
                            f"[cache.pr].expected-open-prs ({expected_open_prs}) = {fleet_worst} B, "
                            f"which exceeds [cache.pr].budget ({ci.cache.pr.budget} = {pr_budget} B)"
                        ),
                        fix=(
                            "lower [cache.pr].max-per-pr, lower [cache.pr].expected-open-prs (if the "
                            "estimate was too high), or raise [cache.pr].budget -- see docs/ci-toml.md "
                            "'Sizing [cache.pr] for repository scale' for the formula"
                        ),
                    )
                )

    measured = ci.cache.pr.measured_largest_delta
    if measured:
        measured_bytes = parse_size(measured)
        if measured_bytes is not None and measured_bytes > max_per_pr:
            findings.append(
                Finding(
                    rule="CACHE-014",
                    path="ci.toml",
                    message=(
                        f"[cache.pr].max-per-pr ({ci.cache.pr.max_per_pr} = {max_per_pr} B) is below "
                        f"[cache.pr].measured-largest-delta ({measured} = {measured_bytes} B): the "
                        "largest observed PR delta would be truncated on save"
                    ),
                    fix=(
                        "raise [cache.pr].max-per-pr to at least [cache.pr].measured-largest-delta "
                        "(re-measure with 'ci-lint cache delta manifest' against the largest real PR "
                        "if this value is stale) -- see docs/ci-toml.md 'Sizing [cache.pr] for "
                        "repository scale'"
                    ),
                )
            )

    return findings


def check_cache_029(ci: CiToml) -> list[Finding]:
    """A large family with nothing bounding how many entries may exist at
    once will fill the repository's whole cache budget.

    Measured across the three largest Rust repositories, 2026-10-04, with
    `ci-lint cache audit` on the one that declares its families:

        kernal-api   49 entries, 9.39 GiB, 87% superseded -> 6.5 GiB reclaimed,
                     3.01 GiB left (it declares `[cache.family]`, so the
                     family boundary is known)
        clud         32 entries, 10.56 GiB -- ALREADY OVER GitHub's 10GB cap,
                     so new saves were being refused; 1.86 GiB recoverable
        running-process 100 entries, 9.16 GiB; 1.01 GiB recoverable

    clud and running-process declare no `[cache.family]`, so their family
    boundaries have to be inferred from the key shape
    (`...-v<n>-<runner>-<lockhash>-<shape>-<contenthash>`, grouping on
    everything but the hex components). That inference is deliberately
    conservative -- it keeps entries rather than guessing -- which is why
    their recoverable fraction is far below kernal-api's.

    The mechanism is the same in all three: these families' keys embed a
    per-run hash, so every run appends an entry nothing ever retires.

    `max` declares the size of ONE entry, not the family's footprint, so it
    cannot catch this. Only `evict = "lru"` bounds it: the janitor keeps the
    family at `max x cardinality`, evicting least-recently-used.

    `per` is deliberately NOT accepted as an exemption. It declares the
    *writer shape*, not the entry count, and kernal-api is the proof:
    every one of its families declares `per = "none"` while `dylint` still
    accumulated 13 live entries. docs/ci-toml.md records the same problem
    for `setup-uv`, whose key encodes arch, platform, OS version, Python
    version and a content hash -- so its live cardinality can exceed what
    `per = "os"` claims.

    Only families at or above 256 MB are flagged: below that, unbounded
    entry counts are noise rather than a budget threat."""

    findings: list[Finding] = []
    for fid, family in sorted(ci.cache.family.items()):
        if family.evict == "lru":
            continue
        declared = parse_size(family.max)
        if declared is None or declared < UNBOUNDED_FAMILY_BYTES:
            continue
        findings.append(
            Finding(
                rule="CACHE-029",
                path="ci.toml",
                message=(
                    f"cache family '{fid}' declares max = {family.max} but nothing bounds how many "
                    f"entries may exist at once, so the family can grow without limit until the "
                    "repository's whole cache budget is consumed"
                ),
                fix=(
                    f"add evict = \"lru\" to '[cache.family.{fid}]' so ci-lint cache janitor keeps "
                    f"the family at max x cardinality. Measure the current state with "
                    f"'ci-lint cache audit --repo .' -- CACHE-006 names the superseded entries that "
                    f"are consuming the budget."
                ),
            )
        )
    return findings


def check_cache_030(ci: CiToml) -> list[Finding]:
    """`[cache.promote] mode = "ancestor"` declares that a default-branch
    push inherits the cache a pull request proved, instead of rewriting it.

    It is a POLICY statement today, not a mechanism. zackees/setup-soldr
    declares an `auto-key` input in its `action.yml` -- "treat an omitted
    key as 'auto' for the source-dependent build-cache pilot" -- but that
    input appears nowhere in setup-soldr's source or in its built bundle
    (`dist/main.js`, `dist/post.js`), and zackees/setup-soldr#552 is still
    open. Declaring `ancestor` today would be a promise nothing keeps, so
    it is reported as needs_review pointing at the implementation, rather
    than accepted silently or refused outright: a repository is allowed to
    record the policy it intends to adopt."""

    if ci.cache.promote.mode != "ancestor":
        return []
    return [
        Finding(
            rule="CACHE-030",
            status=Status.NEEDS_REVIEW,
            path="ci.toml",
            message='\'cache.promote.mode = "ancestor"\' declares nearest-ancestor cache promotion, '
            "but setup-soldr does not implement it yet (the `auto-key` input is declared in its "
            "action.yml and absent from its source and bundle; zackees/setup-soldr#552 is open)",
            fix="record the intent now and revisit once zackees/setup-soldr#552 lands; until then "
            "promotion does not happen, so keep the declared drift backstop (a scheduled full "
            "default-branch run, or enough naturally-must-run pushes) and do not remove it on the "
            "strength of this declaration",
        )
    ]


def check_group9(ci: CiToml, repo_root: Path) -> tuple[list[Finding], str]:
    findings: list[Finding] = []
    findings.extend(check_cache_001(ci, repo_root))
    findings.extend(check_cache_002(ci, repo_root))
    findings.extend(check_cache_003_setup_uv(ci, repo_root))
    findings.extend(check_cache_010(ci, repo_root))
    cache_004_findings, arithmetic = check_cache_004(ci)
    findings.extend(cache_004_findings)
    findings.extend(check_cache_014(ci))
    findings.extend(check_cache_029(ci))
    findings.extend(check_cache_030(ci))
    findings.extend(check_cache_031(ci))
    return findings, arithmetic


def check_cache_031(ci: CiToml) -> list[Finding]:
    """A repository that declares ancestor promotion, but whose families
    cannot support it, is promising something its keys cannot deliver.

    Promotion means: given commit M, find the nearest ancestor's cache entry.
    That search is decidable from the keys alone only when a key carries the
    lineage label (`m<n>[-c<k>]-<sha10>[-pr-<N>]`,
    ci_lint.cache_lineage) -- `m<n>` is the first-parent ordinal, so
    `m<i>` precedes `m<j>` iff i < j. A key that ends in a bare content hash
    (setup-soldr's `...-<16 hex>`) cannot answer the question at all: a
    content hash is a function of the input tree, so nothing in it records
    ancestry. That is not a gap a janitor can bridge -- the evidence is gone
    at save time.

    Measured 2026-10-04 across the fleet's live cache listings: the label is
    adopted exactly where it carries no bytes. soldr has 70 lineage-labeled
    entries totalling 0.00 GiB (all `att1-*` attestation side-entries)
    against 5.97 GiB of bare-hash build caches; bosn 85 / 0.00 GiB against
    4.41 GiB; zccache, clud and kernal-api have none at all.

    This is `needs_review`, never a hard failure, for two reasons. setup-soldr
    does not implement the promotion mechanism yet (its `auto-key` input is
    declared in `action.yml` and absent from its source and bundle;
    zackees/setup-soldr#552 is open), so a violation here may be a repository
    that cannot fix it today rather than one that got it wrong. And a family
    only becomes promotable when its keys change shape -- an instantaneous
    fix would invalidate every warm entry at once, which on repositories
    already at the 10GB cap is a real cost, not a free correction."""

    if ci.cache.promote.mode != "ancestor":
        return []
    findings: list[Finding] = []
    for fid, family in sorted(ci.cache.family.items()):
        if family.promote == "ancestor":
            continue
        declared = parse_size(family.max)
        if declared is None or declared < UNBOUNDED_FAMILY_BYTES:
            continue
        findings.append(
            Finding(
                rule="CACHE-031",
                status=Status.NEEDS_REVIEW,
                path="ci.toml",
                message=(
                    f"'cache.promote.mode = \"ancestor\"' is declared, but cache family '{fid}' "
                    f"(max = {family.max}) does not declare promote = \"ancestor\" on its own keys, "
                    "so its entries carry a content hash rather than a lineage label and no "
                    "nearest-ancestor promotion search can select one"
                ),
                fix=(
                    f"add promote = \"ancestor\" to '[cache.family.{fid}]' and have the workflow "
                    "suffix the key with the label from 'ci-lint attest lineage' "
                    "(m<n>-<sha10> on main, m<b>-c<k>-<sha10>-pr-<N> on a PR, the PR part already "
                    f"being ${{{{ env.PR_CACHE_TAG }}}} per CACHE-013). Blocked on "
                    "zackees/setup-soldr#552 for setup-soldr-owned families; changing a live key "
                    "shape also invalidates that family's warm entries, so schedule it"
                ),
            )
        )
    return findings
