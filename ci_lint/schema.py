"""ci.toml schema 3: frozen dataclasses + strict tomllib loader.

Section A of the round-1A brief. Unknown keys anywhere are `CT-001` (with
the key path); wrong types are `CT-002`; `schema` must equal exactly 3
(`CT-003`). Cross-reference validation (flows' platforms/suites exist, tags
reference existing suites/platforms, `extends` resolves without cycles,
exception fields well-formed) is also reported as `CT-002`, since the brief
does not allocate a separate rule ID for referential-integrity errors and
CT-002 ("wrong types") is the closest existing bucket for "a value that does
not resolve to a valid thing" -- documented as a decision in docs/ci-toml.md.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ci_lint.cache.families import ALLOWED_VIA_VALUES
from ci_lint.finding import Finding
from ci_lint.toml_cursor import Cursor, TomlValue

LINTER_RE = re.compile(r"^zackees/ci\.yml@([0-9a-fA-F]{40})$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
URL_RE = re.compile(r"^https?://")

# [cache.family.<id>].per's cardinality multiplier (ci_lint.rules.cache_static
# ._cardinality): "none" is the default (cardinality 1, matching an absent
# `per`); any other value is a CT-002 schema error rather than a silent
# fallback to cardinality 1 -- round-2A amendment 2. An unrecognized `per`
# (e.g. the template's "target") previously undercounted CACHE-004's
# arithmetic without any indication anything was wrong.
CACHE_FAMILY_PER_VALUES: frozenset[str] = frozenset({"none", "platform", "cross-platform", "os"})


# ── Platforms ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Platform:
    id: str
    target: str
    runs_on: str
    group: str
    wheel: str | None = None


# ── Python packaging ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class CliBinary:
    name: str
    crate: str
    native: bool


@dataclass(frozen=True)
class PythonConfig:
    backend: str
    cli: CliBinary
    abi3: str
    pythons: tuple[str, ...]


# ── Rust workspace discipline ────────────────────────────────────────────


@dataclass(frozen=True)
class RustTests:
    max_binary: str
    max_total: str
    binaries: tuple[str, ...]


@dataclass(frozen=True)
class RustConfig:
    public: str
    private: str
    ship: tuple[tuple[str, ...], ...]
    tests: RustTests | None


# ── Lint lanes ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DylintBudget:
    warm: str
    cold: str


@dataclass(frozen=True)
class DylintConfig:
    targets: str
    shape: str
    budget: DylintBudget


# ── Suites ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Suite:
    id: str
    run: str
    required: bool = False
    cache: str | None = None
    kind: str | None = None
    gating: bool = True


# ── Flows ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FlowBudget:
    critical_path: str | None = None


@dataclass(frozen=True)
class Flow:
    id: str
    extends: str | None = None
    platforms: tuple[str, ...] | str | None = None
    suites: tuple[str, ...] | str | None = None
    dylint: str | None = None
    budget: FlowBudget | None = None
    cache: str | None = None
    schedule: str | None = None
    wheels: str | None = None
    publish: str | None = None
    janitor: bool = False
    pre_prune: bool = False


# ── Tags ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TagAdd:
    platforms: tuple[str, ...] | str | None = None
    suites: tuple[str, ...] | str | None = None


@dataclass(frozen=True)
class TagRemove:
    suites: tuple[str, ...] | str | None = None


@dataclass(frozen=True)
class TagRule:
    id: str
    add: TagAdd | None = None
    remove: TagRemove | None = None
    cache: str | None = None
    flow: str | None = None
    publish: str | None = None


# ── Caches ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PrCache:
    mode: str
    families: tuple[str, ...]
    max_per_pr: str
    budget: str
    trim: str


@dataclass(frozen=True)
class CacheFamily:
    id: str
    via: str
    max: str
    lockfile: bool = False
    per: str | None = None
    min: str | None = None
    key: tuple[str, ...] | None = None
    # zackees/ci.yml#23 §4.2: "lru" makes the family evictable -- the cache
    # janitor LRU-evicts its entries down to its declared footprint
    # (max x cardinality). None (the default) keeps the newest entry per key
    # prefix instead (CACHE-006 superseded-entry cleanup).
    evict: str | None = None


CACHE_FAMILY_EVICT_VALUES: frozenset[str] = frozenset({"lru"})


@dataclass(frozen=True)
class CacheConfig:
    budget: str
    write_on: tuple[str, ...]
    never: tuple[str, ...]
    retired: tuple[str, ...]
    pr: PrCache
    family: dict[str, CacheFamily]


# ── Local (bosn -> act) ──────────────────────────────────────────────────


@dataclass(frozen=True)
class LocalConfig:
    runner: str
    lanes: tuple[str, ...]
    cache: str


# ── Allowlists ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SetupSoldrAllow:
    only_in: str
    require: dict[str, str]


@dataclass(frozen=True)
class SetupUvAllow:
    """Round-4B: `[allow].setup-uv.require` -- checked on every
    `astral-sh/setup-uv` use, anywhere (unlike setup-soldr, there is no
    single wrapper location to restrict it to). A `require` value of
    `"plan"` means the input must be an expression derived from the
    precheck plan rather than a literal (`CACHE-003`; see
    `ci_lint.rules.cache_static.check_cache_003_setup_uv`)."""

    require: dict[str, str]


@dataclass(frozen=True)
class CacheActionsAllow:
    """Round-4B: `[allow].cache-actions.only-in` -- the one directory a raw
    `actions/cache`/`actions/cache/save`/`actions/cache/restore` step may
    appear in (a SHA-pinned composite-action wrapper); everywhere else is
    `CACHE-001`. Defaults to `.github/actions/cache` when the table is
    omitted, so a `ci.toml` predating this key still loads."""

    only_in: str = ".github/actions/cache"


@dataclass(frozen=True)
class AllowConfig:
    workflows: dict[str, tuple[str, ...]]
    actions: tuple[str, ...]
    setup_soldr: SetupSoldrAllow
    tools: tuple[str, ...]
    secrets: tuple[str, ...]
    platform_selector: str
    platform_code: tuple[str, ...]
    # Round-4B additions, all optional/backward-compatible (see _parse_allow).
    permissions: dict[str, tuple[str, ...]]
    setup_uv: SetupUvAllow
    cache_actions: CacheActionsAllow


# ── Publish ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PypiPublish:
    auth: str
    environment: str
    mode: str


@dataclass(frozen=True)
class PublishConfig:
    pypi: PypiPublish | None


# ── Exceptions ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExceptionEntry:
    rule: str
    path: str
    reason: str
    issue: str
    expires: str


# ── Root ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CiToml:
    schema: int
    profile: str
    linter: str
    linter_sha: str | None
    platforms: dict[str, Platform]
    python: PythonConfig | None
    rust: RustConfig | None
    lint_dylint: DylintConfig | None
    suites: dict[str, Suite]
    flows: dict[str, Flow]
    tags: dict[str, TagRule]
    cache: CacheConfig
    local: LocalConfig
    allow: AllowConfig
    publish: PublishConfig
    exceptions: tuple[ExceptionEntry, ...]
    source_path: str


def _parse_platforms(root: Cursor) -> dict[str, Platform]:
    out: dict[str, Platform] = {}
    for pid, raw in root.raw_table_of_tables("platforms", required=True).items():
        sub = Cursor(raw, f"platforms.{pid}", root.findings, root.source)
        target = sub.str_("target")
        runs_on = sub.str_("runs-on")
        group = sub.str_("group")
        wheel = sub.str_("wheel", required=False)
        sub.finish()
        out[pid] = Platform(
            id=pid, target=target or "", runs_on=runs_on or "", group=group or "", wheel=wheel
        )
    return out


def _parse_python(root: Cursor) -> PythonConfig | None:
    raw = root.table_("python", required=False)
    if raw is None:
        return None
    sub = Cursor(raw, "python", root.findings, root.source)
    backend = sub.str_("backend") or ""
    cli_raw = sub.table_("cli", required=True)
    cli = None
    if cli_raw is not None:
        cli_sub = Cursor(cli_raw, "python.cli", root.findings, root.source)
        name = cli_sub.str_("name") or ""
        crate = cli_sub.str_("crate") or ""
        native = cli_sub.bool_("native", required=False, default=True)
        cli_sub.finish()
        cli = CliBinary(name=name, crate=crate, native=bool(native))
    abi3 = sub.str_("abi3") or ""
    pythons = sub.list_str("pythons")
    sub.finish()
    if cli is None:
        cli = CliBinary(name="", crate="", native=False)
    return PythonConfig(backend=backend, cli=cli, abi3=abi3, pythons=pythons)


def _parse_rust(root: Cursor) -> RustConfig | None:
    raw = root.table_("rust", required=False)
    if raw is None:
        return None
    sub = Cursor(raw, "rust", root.findings, root.source)
    public = sub.str_("public") or ""
    private = sub.str_("private") or ""
    ship = sub.list_str_list("ship", required=False)
    tests_raw = sub.table_("tests", required=False)
    tests = None
    if tests_raw is not None:
        tsub = Cursor(tests_raw, "rust.tests", root.findings, root.source)
        max_binary = tsub.str_("max-binary") or ""
        max_total = tsub.str_("max-total") or ""
        binaries = tsub.list_str("binaries", required=False)
        tsub.finish()
        tests = RustTests(max_binary=max_binary, max_total=max_total, binaries=binaries)
    sub.finish()
    return RustConfig(public=public, private=private, ship=ship, tests=tests)


def _parse_lint_dylint(root: Cursor) -> DylintConfig | None:
    lint_raw = root.table_("lint", required=False)
    if lint_raw is None:
        return None
    lint_sub = Cursor(lint_raw, "lint", root.findings, root.source)
    dylint_raw = lint_sub.table_("dylint", required=True)
    result = None
    if dylint_raw is not None:
        dsub = Cursor(dylint_raw, "lint.dylint", root.findings, root.source)
        targets = dsub.str_("targets") or ""
        shape = dsub.str_("shape") or ""
        budget_raw = dsub.table_("budget", required=True)
        budget = DylintBudget(warm="", cold="")
        if budget_raw is not None:
            bsub = Cursor(budget_raw, "lint.dylint.budget", root.findings, root.source)
            warm = bsub.str_("warm") or ""
            cold = bsub.str_("cold") or ""
            bsub.finish()
            budget = DylintBudget(warm=warm, cold=cold)
        dsub.finish()
        result = DylintConfig(targets=targets, shape=shape, budget=budget)
    lint_sub.finish()
    return result


def _parse_suites(root: Cursor) -> dict[str, Suite]:
    out: dict[str, Suite] = {}
    for sid, raw in root.raw_table_of_tables("suites", required=True).items():
        sub = Cursor(raw, f"suites.{sid}", root.findings, root.source)
        run = sub.str_("run") or ""
        required = sub.bool_("required", required=False, default=False)
        cache = sub.str_("cache", required=False)
        kind = sub.str_("kind", required=False)
        gating = sub.bool_("gating", required=False, default=True)
        sub.finish()
        out[sid] = Suite(
            id=sid, run=run, required=bool(required), cache=cache, kind=kind, gating=bool(gating)
        )
    return out


def _parse_flows(root: Cursor) -> dict[str, Flow]:
    out: dict[str, Flow] = {}
    for fid, raw in root.raw_table_of_tables("flow", required=True).items():
        sub = Cursor(raw, f"flow.{fid}", root.findings, root.source)
        extends = sub.str_("extends", required=False)
        platforms = sub.str_or_list("platforms", required=False)
        suites = sub.str_or_list("suites", required=False)
        dylint = sub.str_("dylint", required=False)
        budget_raw = sub.table_("budget", required=False)
        budget = None
        if budget_raw is not None:
            bsub = Cursor(budget_raw, f"flow.{fid}.budget", root.findings, root.source)
            critical_path = bsub.str_("critical-path", required=False)
            bsub.finish()
            budget = FlowBudget(critical_path=critical_path)
        cache = sub.str_("cache", required=False)
        schedule = sub.str_("schedule", required=False)
        wheels = sub.str_("wheels", required=False)
        publish = sub.str_("publish", required=False)
        janitor = sub.bool_("janitor", required=False, default=False)
        pre_prune = sub.bool_("pre-prune", required=False, default=False)
        sub.finish()
        out[fid] = Flow(
            id=fid,
            extends=extends,
            platforms=platforms,
            suites=suites,
            dylint=dylint,
            budget=budget,
            cache=cache,
            schedule=schedule,
            wheels=wheels,
            publish=publish,
            janitor=bool(janitor),
            pre_prune=bool(pre_prune),
        )
    return out


def _parse_tags(root: Cursor) -> dict[str, TagRule]:
    out: dict[str, TagRule] = {}
    for tid, raw in root.raw_table_of_tables("tags", required=True).items():
        sub = Cursor(raw, f"tags.{tid}", root.findings, root.source)
        add_raw = sub.table_("add", required=False)
        add = None
        if add_raw is not None:
            asub = Cursor(add_raw, f"tags.{tid}.add", root.findings, root.source)
            add_platforms = asub.str_or_list("platforms", required=False)
            add_suites = asub.str_or_list("suites", required=False)
            asub.finish()
            add = TagAdd(platforms=add_platforms, suites=add_suites)
        remove_raw = sub.table_("remove", required=False)
        remove = None
        if remove_raw is not None:
            rsub = Cursor(remove_raw, f"tags.{tid}.remove", root.findings, root.source)
            remove_suites = rsub.str_or_list("suites", required=False)
            rsub.finish()
            remove = TagRemove(suites=remove_suites)
        cache = sub.str_("cache", required=False)
        flow = sub.str_("flow", required=False)
        publish = sub.str_("publish", required=False)
        sub.finish()
        out[tid] = TagRule(id=tid, add=add, remove=remove, cache=cache, flow=flow, publish=publish)
    return out


def _parse_cache(root: Cursor) -> CacheConfig:
    raw = root.table_("cache", required=True)
    if raw is None:
        return CacheConfig(
            budget="0GB",
            write_on=(),
            never=(),
            retired=(),
            pr=PrCache(mode="", families=(), max_per_pr="", budget="", trim=""),
            family={},
        )
    sub = Cursor(raw, "cache", root.findings, root.source)
    budget = sub.str_("budget") or "0GB"
    write_on = sub.list_str("write-on", required=False)
    never = sub.list_str("never", required=False)
    retired = sub.list_str("retired", required=False)
    pr_raw = sub.table_("pr", required=True)
    pr = PrCache(mode="", families=(), max_per_pr="", budget="", trim="")
    if pr_raw is not None:
        psub = Cursor(pr_raw, "cache.pr", root.findings, root.source)
        mode = psub.str_("mode") or ""
        families = psub.list_str("families", required=False)
        max_per_pr = psub.str_("max-per-pr") or ""
        pr_budget = psub.str_("budget") or ""
        trim = psub.str_("trim") or ""
        psub.finish()
        pr = PrCache(mode=mode, families=families, max_per_pr=max_per_pr, budget=pr_budget, trim=trim)
    family: dict[str, CacheFamily] = {}
    for fam_id, fam_raw in sub.raw_table_of_tables("family", required=True).items():
        fsub = Cursor(fam_raw, f"cache.family.{fam_id}", root.findings, root.source)
        via = fsub.str_("via") or ""
        if via and via not in ALLOWED_VIA_VALUES:
            root.findings.append(
                Finding(
                    rule="CT-002",
                    path=root.source,
                    message=f"'cache.family.{fam_id}.via' = {via!r} is not one of "
                    f"{sorted(ALLOWED_VIA_VALUES)}",
                    fix=f"set 'cache.family.{fam_id}.via' to a recognized value (see docs/ci-toml.md's "
                    "cache-family table) -- a new setup-soldr-owned family needs a new "
                    "ci_lint.cache.families.FamilyShape entry, cited against setup-soldr's source, "
                    "before ci.toml can declare it",
                )
            )
        max_ = fsub.str_("max") or ""
        lockfile = fsub.bool_("lockfile", required=False, default=False)
        per = fsub.str_("per", required=False)
        if per is not None and per not in CACHE_FAMILY_PER_VALUES:
            root.findings.append(
                Finding(
                    rule="CT-002",
                    path=root.source,
                    message=f"'cache.family.{fam_id}.per' = {per!r} is not one of "
                    f"{sorted(CACHE_FAMILY_PER_VALUES)}",
                    fix=f"set 'cache.family.{fam_id}.per' to one of: "
                    + ", ".join(sorted(CACHE_FAMILY_PER_VALUES))
                    + " (or omit it, which defaults to 'none')",
                )
            )
        min_ = fsub.str_("min", required=False)
        key = fsub.list_str("key", required=False)
        evict = fsub.str_("evict", required=False)
        if evict is not None and evict not in CACHE_FAMILY_EVICT_VALUES:
            root.findings.append(
                Finding(
                    rule="CT-002",
                    path=root.source,
                    message=f"'cache.family.{fam_id}.evict' = {evict!r} is not one of "
                    f"{sorted(CACHE_FAMILY_EVICT_VALUES)}",
                    fix=f"set 'cache.family.{fam_id}.evict' to \"lru\" (an evictable family the janitor "
                    "LRU-trims to its budget) or omit it (keep the newest entry per key prefix)",
                )
            )
        fsub.finish()
        family[fam_id] = CacheFamily(
            id=fam_id, via=via, max=max_, lockfile=bool(lockfile), per=per, min=min_, key=key or None,
            evict=evict,
        )
    sub.finish()
    return CacheConfig(
        budget=budget, write_on=write_on, never=never, retired=retired, pr=pr, family=family
    )


def _parse_local(root: Cursor) -> LocalConfig:
    raw = root.table_("local", required=True)
    if raw is None:
        return LocalConfig(runner="", lanes=(), cache="")
    sub = Cursor(raw, "local", root.findings, root.source)
    runner = sub.str_("runner") or ""
    lanes = sub.list_str("lanes")
    cache = sub.str_("cache") or ""
    sub.finish()
    return LocalConfig(runner=runner, lanes=lanes, cache=cache)


def _parse_allow(root: Cursor) -> AllowConfig:
    raw = root.table_("allow", required=True)
    if raw is None:
        return AllowConfig(
            workflows={},
            actions=(),
            setup_soldr=SetupSoldrAllow(only_in="", require={}),
            tools=(),
            secrets=(),
            platform_selector="",
            platform_code=(),
            permissions={},
            setup_uv=SetupUvAllow(require={}),
            cache_actions=CacheActionsAllow(),
        )
    sub = Cursor(raw, "allow", root.findings, root.source)
    workflows_raw = sub.table_("workflows", required=True)
    workflows: dict[str, tuple[str, ...]] = {}
    if workflows_raw is not None:
        for wf_name, triggers in workflows_raw.items():
            if not isinstance(triggers, list) or not all(isinstance(t, str) for t in triggers):
                root.findings.append(
                    Finding(
                        rule="CT-002",
                        path=root.source,
                        message=f"'allow.workflows.{wf_name}' must be an array of strings",
                        fix=f"set 'allow.workflows.\"{wf_name}\"' to an array of trigger strings in {root.source}",
                    )
                )
                continue
            workflows[wf_name] = tuple(triggers)
    actions = sub.list_str("actions")
    ss_raw = sub.table_("setup-soldr", required=True)
    setup_soldr = SetupSoldrAllow(only_in="", require={})
    if ss_raw is not None:
        ssub = Cursor(ss_raw, "allow.setup-soldr", root.findings, root.source)
        only_in = ssub.str_("only-in") or ""
        require = ssub.dict_str_str("require", required=False)
        ssub.finish()
        setup_soldr = SetupSoldrAllow(only_in=only_in, require=require)
    tools = sub.list_str("tools")
    secrets = sub.list_str("secrets", required=False)
    platform_selector = sub.str_("platform-selector") or ""
    platform_code = sub.list_str("platform-code")

    # Round-4B additions. Each is optional so a ci.toml predating this
    # round still loads unchanged (an absent table means "nothing extra
    # allowed" for permissions/setup-uv, and the documented default
    # wrapper directory for cache-actions).
    permissions = sub.dict_str_list_str("permissions", required=False)

    setup_uv_raw = sub.table_("setup-uv", required=False)
    setup_uv = SetupUvAllow(require={})
    if setup_uv_raw is not None:
        uvsub = Cursor(setup_uv_raw, "allow.setup-uv", root.findings, root.source)
        uv_require = uvsub.dict_str_str("require", required=False)
        uvsub.finish()
        setup_uv = SetupUvAllow(require=uv_require)

    cache_actions_raw = sub.table_("cache-actions", required=False)
    cache_actions = CacheActionsAllow()
    if cache_actions_raw is not None:
        casub = Cursor(cache_actions_raw, "allow.cache-actions", root.findings, root.source)
        only_in = casub.str_("only-in", required=False, default=CacheActionsAllow().only_in)
        casub.finish()
        cache_actions = CacheActionsAllow(only_in=only_in or CacheActionsAllow().only_in)

    sub.finish()
    return AllowConfig(
        workflows=workflows,
        actions=actions,
        setup_soldr=setup_soldr,
        tools=tools,
        secrets=secrets,
        platform_selector=platform_selector,
        platform_code=platform_code,
        permissions=permissions,
        setup_uv=setup_uv,
        cache_actions=cache_actions,
    )


def _parse_publish(root: Cursor) -> PublishConfig:
    raw = root.table_("publish", required=True)
    if raw is None:
        return PublishConfig(pypi=None)
    sub = Cursor(raw, "publish", root.findings, root.source)
    pypi_raw = sub.table_("pypi", required=False)
    pypi = None
    if pypi_raw is not None:
        psub = Cursor(pypi_raw, "publish.pypi", root.findings, root.source)
        auth = psub.str_("auth") or ""
        environment = psub.str_("environment") or ""
        mode = psub.str_("mode") or ""
        psub.finish()
        pypi = PypiPublish(auth=auth, environment=environment, mode=mode)
    sub.finish()
    return PublishConfig(pypi=pypi)


def _parse_exceptions(root: Cursor) -> tuple[ExceptionEntry, ...]:
    out: list[ExceptionEntry] = []
    for idx, raw in enumerate(root.array_of_tables("exceptions")):
        sub = Cursor(raw, f"exceptions[{idx}]", root.findings, root.source)
        rule = sub.str_("rule") or ""
        path = sub.str_("path") or ""
        reason = sub.str_("reason") or ""
        issue = sub.str_("issue") or ""
        expires = sub.str_("expires") or ""
        sub.finish()
        if issue and not URL_RE.match(issue):
            root.findings.append(
                Finding(
                    rule="CT-002",
                    path=root.source,
                    message=f"'exceptions[{idx}].issue' is not a URL: {issue!r}",
                    fix=f"set 'exceptions[{idx}].issue' to a https:// issue URL in {root.source}",
                )
            )
        if expires and not DATE_RE.match(expires):
            root.findings.append(
                Finding(
                    rule="CT-002",
                    path=root.source,
                    message=f"'exceptions[{idx}].expires' is not YYYY-MM-DD: {expires!r}",
                    fix=f"set 'exceptions[{idx}].expires' to a YYYY-MM-DD date in {root.source}",
                )
            )
        out.append(ExceptionEntry(rule=rule, path=path, reason=reason, issue=issue, expires=expires))
    return tuple(out)


def load_ci_toml(repo_root: Path) -> tuple[CiToml | None, list[Finding]]:
    """Load and strictly validate ci.toml. Returns (None, findings) on failure
    to even parse; otherwise (CiToml, findings-from-validation)."""

    findings: list[Finding] = []
    path = repo_root / "ci.toml"
    source = "ci.toml"
    if not path.is_file():
        findings.append(
            Finding(
                rule="CT-002",
                path=source,
                message="ci.toml not found at repo root",
                fix="create ci.toml at the repo root implementing schema 3 (see docs/ci-toml.md)",
            )
        )
        return None, findings

    try:
        with path.open("rb") as fh:
            raw_root = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        findings.append(
            Finding(
                rule="CT-002",
                path=source,
                message=f"ci.toml is not valid TOML: {exc}",
                fix="fix the TOML syntax error reported above",
            )
        )
        return None, findings

    root = Cursor(raw_root, "", findings, source)

    schema_val = root.int_("schema")
    if schema_val is not None and schema_val != 3:
        findings.append(
            Finding(
                rule="CT-003",
                path=source,
                message=f"schema = {schema_val}, expected 3",
                fix="set 'schema = 3' in ci.toml (this checker implements schema 3 only)",
            )
        )
    profile = root.str_("profile") or ""
    linter = root.str_("linter") or ""
    linter_sha: str | None = None
    m = LINTER_RE.match(linter)
    if linter and m is None:
        findings.append(
            Finding(
                rule="CT-002",
                path=source,
                message=f"'linter' must match 'zackees/ci.yml@<40-hex-sha>', got {linter!r}",
                fix="set 'linter' to \"zackees/ci.yml@<40-char commit SHA>\" in ci.toml",
            )
        )
    elif m is not None:
        linter_sha = m.group(1).lower()

    platforms = _parse_platforms(root)
    python = _parse_python(root)
    rust = _parse_rust(root)
    lint_dylint = _parse_lint_dylint(root)
    suites = _parse_suites(root)
    flows = _parse_flows(root)
    tags = _parse_tags(root)
    cache = _parse_cache(root)
    local = _parse_local(root)
    allow = _parse_allow(root)
    publish = _parse_publish(root)
    exceptions = _parse_exceptions(root)

    root.finish()

    ci = CiToml(
        schema=schema_val or 0,
        profile=profile,
        linter=linter,
        linter_sha=linter_sha,
        platforms=platforms,
        python=python,
        rust=rust,
        lint_dylint=lint_dylint,
        suites=suites,
        flows=flows,
        tags=tags,
        cache=cache,
        local=local,
        allow=allow,
        publish=publish,
        exceptions=exceptions,
        source_path=source,
    )

    findings.extend(_validate_cross_refs(ci))
    return ci, findings


def _validate_cross_refs(ci: CiToml) -> list[Finding]:
    out: list[Finding] = []
    source = ci.source_path

    def bad(msg: str, fix: str) -> None:
        out.append(Finding(rule="CT-002", path=source, message=msg, fix=fix))

    # extends cycle / resolution
    for fid, flow in ci.flows.items():
        seen = {fid}
        cur = flow
        while cur.extends is not None:
            if cur.extends not in ci.flows:
                bad(
                    f"'flow.{fid}' extends unknown flow '{cur.extends}'",
                    f"set 'flow.{fid}.extends' to an existing [flow.*] id, or remove it",
                )
                break
            if cur.extends in seen:
                bad(
                    f"'flow.{fid}' extends cycle: {' -> '.join([*seen, cur.extends])}",
                    f"break the extends cycle starting at 'flow.{fid}'",
                )
                break
            seen.add(cur.extends)
            cur = ci.flows[cur.extends]

    def check_platform_ref(where: str, value: tuple[str, ...] | str | None) -> None:
        if value is None or value == "all":
            return
        for pid in value:
            if pid not in ci.platforms:
                bad(
                    f"'{where}' references unknown platform '{pid}'",
                    f"declare '[platforms.{pid}]' in ci.toml, or fix the id at '{where}'",
                )

    def check_suite_ref(where: str, value: tuple[str, ...] | str | None) -> None:
        if value is None or value in ("all", "tests"):
            return
        for sid in value:
            if sid not in ci.suites:
                bad(
                    f"'{where}' references unknown suite '{sid}'",
                    f"declare '[suites.{sid}]' in ci.toml, or fix the id at '{where}'",
                )

    for fid, flow in ci.flows.items():
        check_platform_ref(f"flow.{fid}.platforms", flow.platforms)
        check_suite_ref(f"flow.{fid}.suites", flow.suites)

    for tid, tag in ci.tags.items():
        if tag.add is not None:
            check_platform_ref(f"tags.{tid}.add.platforms", tag.add.platforms)
            check_suite_ref(f"tags.{tid}.add.suites", tag.add.suites)
        if tag.remove is not None:
            check_suite_ref(f"tags.{tid}.remove.suites", tag.remove.suites)
        if tag.flow is not None and tag.flow not in ci.flows:
            bad(
                f"'tags.{tid}.flow' references unknown flow '{tag.flow}'",
                f"declare '[flow.{tag.flow}]' in ci.toml, or fix 'tags.{tid}.flow'",
            )

    # profile requirements (section A)
    if ci.profile == "rust-pypi-app":
        if ci.python is None:
            bad(
                "profile 'rust-pypi-app' requires a [python] table",
                "add [python] with backend = \"soldr\" and [python.cli]",
            )
        elif ci.python.backend != "soldr":
            bad(
                f"[python].backend must be \"soldr\" for profile 'rust-pypi-app', got {ci.python.backend!r}",
                "set 'python.backend = \"soldr\"'",
            )
        if ci.rust is None:
            bad(
                "profile 'rust-pypi-app' requires a [rust] table",
                "add [rust] with public/private/ship",
            )
        required_suites = {sid for sid, s in ci.suites.items() if s.required}
        if "unit" not in required_suites:
            bad(
                "profile 'rust-pypi-app' requires suites.unit.required = true",
                "set '[suites.unit] required = true' in ci.toml",
            )
        if "smoke" not in required_suites:
            bad(
                "profile 'rust-pypi-app' requires suites.smoke.required = true",
                "set '[suites.smoke] required = true' in ci.toml",
            )
        if ci.publish.pypi is None or ci.publish.pypi.auth != "oidc":
            bad(
                "profile 'rust-pypi-app' requires [publish.pypi].auth = \"oidc\"",
                "set '[publish.pypi] auth = \"oidc\"' in ci.toml",
            )

    return out
