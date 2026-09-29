# `ci.toml` schema 3 reference

This is the field-by-field reference for `ci.toml` schema 3, the contract
`ci_lint` (this repository) loads, validates, plans against, and (from
round-2A) checks a real build's output against. It documents what
[issue #6](https://github.com/zackees/ci.yml/issues/6) implements so far:

- round-1A: the `ci_lint` package's static **precheck** and its **planner**.
- round-2A: the runtime commands that need a real build, wheel or venv --
  `python3 -m ci_lint units`, `tests size`, `wheel check`, `wheel
  installed`, and `gate` (the `CI OK` aggregator) -- plus `precheck
  --local` (skips checks needing the GitHub API, explicitly) and
  `selftest` (runs this package's own test suite; see
  zackees/zccache#1760).
- round-3A: the planner's platform-lane matrix outputs (`platform_lanes`,
  `fast_suites`, `lane_digests` -- see "The planner" below); title-edit
  **reuse** (`ci_lint plan --reuse`, `ci_lint precheck --reuse
  --github-output`; see "Title-edit reuse"); and `ci_lint gate --reuse`'s
  live reuse verification (see "Reuse verification" under "Runtime
  commands").
- round-4A: the cache runtime (`python3 -m ci_lint cache ...`) -- key
  building, `save-ok` (issue #6 §6's do-not-save table, CACHE-008), the
  live `audit` (CACHE-001/003/005/006/008/009 + the budget check),
  `trim`/`janitor`/`heal`/`preprune` (live deletes), PR delta mechanics
  (`cache delta manifest|pack|apply`), and `precheck --live`. See "Cache
  runtime (round-4A)" below.
- round-4B: per-job permission grants (`[allow].permissions`, `SEC-002`
  refinement); the sanctioned `actions/cache*` wrapper (`[allow].cache-
  actions`, `CACHE-001`/`CACHE-002` refinement); plan-driven cache saves
  (`[allow].setup-soldr.require`/`[allow].setup-uv.require` values may be
  the special string `"plan"`, and the planner's `cache_save` `--github-
  output` key); and the static `GEN-004` implementation (Ruff + Pylint in
  the `fast` job, no standalone Black/isort).
- round-5: `ci_lint audit` (`python3 -m ci_lint audit`) -- a live,
  read-only settings/secrets audit (`SEC-005`/`006`/`007`, `GEN-006`,
  `GEN-011`; see "Settings audit (round-5)" below) -- replaces the stub
  that previously exited 2. `ci_lint publish oidc-check` (`ci_lint.
  publish_oidc`) -- issue #6 §7's mock publisher: mints and asserts the
  claims of the OIDC token, never prints it, and stops before upload.
  `ci_lint release verify` (`ci_lint.release`) -- issue #6 §3/ci.yml#4's
  release-candidate gate: the staged wheel+sdist set must exactly match
  `[platforms]`, share one version, each pass the existing `wheel check`,
  and (optionally) have a passing `--smoke` result; writes `release-
  manifest.json` (`PKG-006`). `ci_lint perf compare` (`ci_lint.perf`) --
  a baseline/current benchmark comparison, non-gating unless `[suites.
  perf].gating = true` and `--threshold-pct` is given.

`ci_lint audit` (round-5) is a live, read-only **settings/secrets** audit
-- `SEC-005`/`006`/`007`, `GEN-006`, `GEN-011` -- see "Settings audit
(round-5)" below. It is unrelated to `ci_lint cache audit` (round-4A,
below), which classifies GitHub Actions cache entries. proposal.md's
broader "ci-lint history" idea (comparing declared coverage against
*observed* GitHub run history over time) is still future scope; round-5
only covers the settings/secrets slice the round-5 brief named.

The canonical example is [`examples/rust-pypi-app/ci.toml`](../examples/rust-pypi-app/ci.toml),
copied verbatim from the design draft in
[issue #6](https://github.com/zackees/ci.yml/issues/6). Where this document
and that example ever disagree, treat it as a bug in this document.

`ci.toml` never generates YAML: it bounds what a repository's own
`.github/workflows/*.yml` may do, and decides what each CI run selects.

## Loading and strictness

`ci_lint.schema.load_ci_toml` reads `ci.toml` with `tomllib` into frozen
dataclasses. Loading is **strict**:

- An unknown key anywhere in the file (including inside a nested table) is
  `CT-001`, reported with the key's full dotted path (e.g.
  `platforms.linux-x64.bogus`).
- A key whose TOML value has the wrong type -- including a required key
  that is simply missing -- is `CT-002`. `CT-002` also covers referential
  integrity: a `flow`/`tags` entry that names a platform, suite, or flow
  that does not exist; an `extends` cycle; and a malformed `[[exceptions]]`
  entry (`issue` not a URL, `expires` not `YYYY-MM-DD`). The brief does not
  allocate separate rule IDs for these referential checks, so they share
  `CT-002` ("a value that does not resolve to a valid thing"); this is a
  round-1A decision, not a numbering the design fixed elsewhere.
- `schema` must equal exactly `3`; any other value is `CT-003`.

## Top-level keys

| Key | Type | Meaning |
| --- | --- | --- |
| `schema` | int | Must be `3`. |
| `profile` | string | e.g. `"rust-pypi-app"`. Only `rust-pypi-app` has enforced requirements in round 1A (below); other profiles load but are not yet cross-checked. |
| `linter` | string | `"zackees/ci.yml@<40-hex-sha>"` -- the exact `ci-lint` commit precheck runs. Validated by regex at load time; the workflow's actual checkout of `zackees/ci.yml` is cross-checked against it by `CT-004`. |

## `[platforms.<id>]`

One entry per platform id (`linux-x64`, `windows-arm64`, ...). Each id
generates a derived tag `ci-<id>`; each distinct `group` generates a
derived tag `ci-<group>`.

| Field | Type | Meaning |
| --- | --- | --- |
| `target` | string | Rust target triple, e.g. `x86_64-unknown-linux-gnu`. Cross-checked against `Cargo.toml`'s `[workspace.metadata.soldr].targets` by `CT-006`. |
| `runs-on` | string | A fleet runner label (see `RUN-001`'s fleet list below). |
| `group` | string | e.g. `linux`, `windows`, `macos`. Platforms sharing a group share a `ci-<group>` tag and an `os`-scoped cache cardinality. |
| `wheel` | string (optional) | e.g. `manylinux_2_17`. Informational in round 1A. |

## `[python]`

Required when `profile = "rust-pypi-app"`.

| Field | Type | Meaning |
| --- | --- | --- |
| `backend` | string | Must be `"soldr"` for the `rust-pypi-app` profile. |
| `cli.name` | string | The command the wheel installs on `PATH`. Cross-checked against `[project.scripts]`/`[project.gui-scripts]` (`PKG-003`: must NOT be present there -- the command must be the bundled native binary, not a Python shim). |
| `cli.crate` | string | The Rust crate bundled as that CLI. Cross-checked against `[tool.soldr.pep517].bundle-bins` (`PKG-003`: must be present there). |
| `cli.native` | bool (default `true`) | Informational in round 1A. |
| `abi3` | string | e.g. `"cp310"`. Informational in round 1A. |
| `pythons` | array of strings | e.g. `["3.11", "3.14"]`. Informational in round 1A. |

## `[rust]`

Required when `profile = "rust-pypi-app"`.

| Field | Type | Meaning |
| --- | --- | --- |
| `public` | string | The repo-relative directory of the public "amalgam" crate: re-exports and `dep:` feature wiring only. Its `[features]` are checked by `RUST-011`. |
| `private` | string (glob) | e.g. `"crates/private/*"`. Every matching crate is checked by `RUST-011`: `publish = false`, no `[features]`, no optional dependencies, no `cfg(feature)` in its sources. |
| `ship` | array of string arrays | The **only** amalgam feature sets ever compiled, e.g. `[[], ["json"]]`. Any `--features`/`--all-features`/`cargo hack`/`--feature-powerset` outside this set is `RUST-011`. |

### `[rust.tests]`

| Field | Type | Meaning |
| --- | --- | --- |
| `max-binary` | string (size) | Informational in round 1A (no runtime measurement yet). |
| `max-total` | string (size) | Informational in round 1A. |
| `binaries` | array of strings | The declared test-binary names, in the form `<crate>:lib`, `<crate>:bin:<name>`, or `<crate>:test:<name>` -- see `RUST-005`/`RUST-012` below. This is the fan-out budget: every test binary Cargo would actually build must appear here, and every entry here must correspond to something Cargo actually builds. |

## `[lint.dylint]`

| Field | Type | Meaning |
| --- | --- | --- |
| `targets` | string | `"all-platforms"` in round 1A (the only value the planner understands: dylint runs once per declared platform, all on Linux). |
| `shape` | string | `"multi-target"` \| `"sequential"` \| `"per-target-jobs"` (`"measure"` while undecided). The template measured multi-target faster than sequential in round 3B (template-python-rust-cmd#19); not enforced by precheck. |
| `budget.warm` / `budget.cold` | string (duration) | Informational in round 1A. |

## `[suites.<id>]`

One entry per suite id. Each id generates derived tags `ci-test-<suite>`
and `no-test-<suite>`.

| Field | Type | Meaning |
| --- | --- | --- |
| `run` | string | The command that runs the suite (e.g. `"ci/test.py unit"`). Not executed by precheck; informational/plan-only in round 1A. |
| `required` | bool (default `false`) | The `rust-pypi-app` profile requires a `unit` and a `smoke` suite with `required = true`. |
| `cache` | string (optional) | e.g. `"none"` for a suite that proves caches mask nothing. Informational in round 1A. |
| `kind` | string (optional) | e.g. `"bench"`. Informational in round 1A. |
| `gating` | bool (default `true`) | Whether a failure blocks merge. Informational in round 1A. |

If a suite id is exactly `perf`, a derived tag `ci-perf-<group>` is added
for every platform group.

## `[flow.<id>]`

One entry per flow id (`pr`, `main`, `release`, `nightly`, ...).

| Field | Type | Meaning |
| --- | --- | --- |
| `extends` | string (optional) | Another flow id. Fields not set on this flow inherit the extended flow's resolved value (base-to-derived merge; see `ci_lint.resolve.resolve_flow`). A cycle is `CT-002`. |
| `platforms` | `"all"` or array of platform ids | The base platform selection. |
| `suites` | `"all"` or array of suite ids | The base suite selection. |
| `dylint` | string (optional) | e.g. `"all-platforms"`. |
| `budget.critical-path` | string (duration, optional) | Informational in round 1A. |
| `cache` | string (optional) | `"write"` marks this flow as a cache **writer** (only `main` and `nightly` should set this -- see `[cache].write-on`). Planner: `cache_mode` is `"write"` when the resolved flow sets this, else `"read"`. |
| `schedule` | string (optional) | e.g. `"daily"`. Selects this flow for the `schedule` event. |
| `wheels` | string (optional) | e.g. `"all"`. Informational in round 1A. |
| `publish` | string (optional) | `"pypi"` (the real release path -- mapped through `[publish.pypi].mode`), `"rehearsal"`, or `"none"`. |
| `janitor` | bool (default `false`) | Marks a flow that runs the cache janitor. Informational in round 1A (no runtime implementation yet). |
| `pre-prune` | bool (default `false`) | A writer flow that prunes stale cache entries before writing. When **every** writer flow (`cache = "write"`) sets `pre-prune = true`, `CACHE-004`'s worst-case sum drops the lockfile-change-peak term (`worst = steady + [cache.pr].budget`); the budget-exceeded violation still fires if that smaller sum still exceeds `[cache].budget`. |

## `[tags.<id>]`

Declares a non-derived tag, matched against `[<id>]` tokens in the PR
title. `ci-full`, `ci-perf`, `ci-cache-save`, `no-test`, and `release` are
the tags the canonical example declares; a repository may declare more.

| Field | Type | Meaning |
| --- | --- | --- |
| `add.platforms` | `"all"` or array of platform ids (optional) | Union into the selection. |
| `add.suites` | array of suite ids (optional) | Union into the selection. |
| `remove.suites` | `"tests"` (sentinel: every suite) or array of suite ids (optional) | Subtracted from the selection. Any tag with a `remove` clause that matches makes the plan `mergeable = false`. |
| `cache` | string (optional) | e.g. `"pr-base"`. Informational in round 1A. |
| `flow` | string (optional) | Switches the selected flow entirely (e.g. `release` switches a PR run onto the `release` flow -- a release rehearsal). |
| `publish` | string (optional) | Overrides the resolved flow's `publish`. |

Besides declared tags, the planner and `TAG-001` also recognize **derived**
tags that need no `[tags.*]` entry: `ci-<platform-id>` and `ci-<group>` (add
platforms), `ci-test-<suite>` (add a suite), `no-test-<suite>` (remove one
suite; also sets `mergeable = false`), and `ci-perf-<group>` (add the `perf`
suite, when one is declared).

## `[cache]`

| Field | Type | Meaning |
| --- | --- | --- |
| `budget` | string (size) | The declared total cache footprint. `CACHE-004` fails if it exceeds 10GB (GitHub's default per-repo cap) regardless of anything else. |
| `write-on` | array of strings | Which flows may write base cache layers, e.g. `["main", "nightly"]`. Informational in round 1A (not yet cross-checked against `[flow.*].cache`). |
| `never` | array of strings | Cache families that must never exist, e.g. `linked-tests`, `target-dir`. Informational in round 1A. |
| `retired` | array of strings | Retired cache families. `CACHE-009` fails if any `zackees/setup-soldr` input enables one. |
| `pr.mode` | string | e.g. `"delta"`. |
| `pr.families` | array of strings | Which families a PR may save a delta for. |
| `pr.max-per-pr` | string (size) | Per-PR delta cap. |
| `pr.budget` | string (size) | Counted once in `CACHE-004`'s worst-case sum. |
| `pr.trim` | string | e.g. `"on-close"`. Informational in round 1A. |

### `[cache.family.<id>]`

| Field | Type | Meaning |
| --- | --- | --- |
| `via` | string | Which builder owns this family's real cache-key shape, and (round-4A) which prefix `ci_lint.cache.audit` expects a live key to start with. A **strict allowlist** (`ci_lint.cache.families.ALLOWED_VIA_VALUES`; unrecognized value is `CT-002`) -- see "Cache runtime (round-4A)" below for the full via -> prefix table. |
| `max` | string (size) | The per-instance cap, used directly in `CACHE-004`'s arithmetic and (round-4A) `cache save-ok`'s rule 6 and `cache preprune`'s live forecast. |
| `lockfile` | bool (default `false`) | If true, this family's steady-state size is counted **twice** in `CACHE-004` (steady state, plus the lockfile-change peak where old and new coexist), and (round-4A) `cache preprune` only prunes superseded entries of `lockfile = true` families. |
| `per` | string (optional) | Cardinality multiplier. A **strict enum**: `"none"` (the default, same as omitting it) -> `1`; `"platform"` -> number of declared platforms; `"cross-platform"` -> platforms minus one; `"os"` -> number of distinct platform groups. Any other value (e.g. `"target"`) is `CT-002` -- never a silent fallback to `1`, which would undercount `CACHE-004`'s arithmetic without warning. |
| `min` | string (size, optional) | Round-4A: the poison-guard floor `cache save-ok`'s rule 6 and the live `cache audit`'s `CACHE-005` both check payload/entry size against. A live entry at or below 1KB is always poisoned regardless of whether a family declares `min` at all. |
| `evict` | string (optional) | zackees/ci.yml#23 §4.2: `"lru"` marks an evictable family (e.g. experiment lanes) -- `cache janitor` LRU-evicts its entries, least recently used first, down to its declared footprint (`max` × cardinality), always keeping its single newest entry. Omitted (the default): the janitor keeps the newest entry per key prefix (`CACHE-006`). Any other value is `CT-002`. |
| `key` | array of strings (optional) | Key components, e.g. `["os", "python", "uv.lock"]`. Scanned by `CACHE-002` for volatile tokens the same way a workflow's `with.key` is, and (round-4A, `via = "ci-lint"` families only) built by `ci-lint cache key <family>` -- `"os"` resolves to `--platform`'s id, `"python"` to `[python].pythons[0]` (the floor version), anything else to the first-16-hex-chars of that repo-relative file's sha256. |

`CACHE-004`'s formula: `worst = Σ(family.max × cardinality) + Σ(family.max × cardinality, families with lockfile=true) + [cache.pr].budget` -- unless **every** writer flow (`cache = "write"`) sets `pre-prune = true`, in which case the middle (lockfile-change-peak) term is dropped: `worst = Σ(family.max × cardinality) + [cache.pr].budget`. Either way, `worst > [cache].budget` is always a violation; pre-prune narrows the sum, it never waives the comparison. Precheck always prints the arithmetic and which formula it applied.

## Cache runtime (round-4A)

`python3 -m ci_lint cache ...` (`ci_lint.cache.*`), built on issue #6 §6
("Cache strategy"). Every live command is stdlib `urllib` through an
injectable `FetchFn`/`DeleteFn`/`GraphQLFn` (`ci_lint.github_api`), exactly
like round-3A's reuse lookup -- no test in this package ever touches the
network; fixtures live under
[`ci_lint/tests/fixtures/runtime/cache/`](../ci_lint/tests/fixtures/runtime/cache/)
(one, `caches-live-template.json`, copied verbatim from `gh api
repos/zackees/template-python-rust-cmd/actions/caches`; the others are
small synthetic API-shaped JSON built to exercise a rule the live repo
doesn't currently exhibit -- each file's own `"_note"` key says which).

### Family resolution table

`ci_lint.cache.families.EXTERNAL_FAMILY_SHAPES` -- every `via` value a
`[cache.family.<id>]` may declare, and the literal GitHub Actions
cache-key prefix it resolves to. Each row is cited against the exact line
of that action's own source that builds it (`setup-soldr:*` from the
reference clone named in the round-4A brief, verified 2026-09-28;
`setup-uv` read read-only via `gh api repos/astral-sh/setup-uv/contents/...`,
verified 2026-09-29 -- both actions are READ-ONLY for this worker); `via =
"ci-lint"` is this package's own convention (no external source to cite),
and `delta` is the PR-delta wrapper (`ci_lint.cache.keys.build_delta_key`),
not a `[cache.family].via` value at all.

| `via` | Key prefix | Source |
| --- | --- | --- |
| `setup-soldr:build-cache` | `setup-soldr-buildcache-v2-` | `resolve-setup.ts:829` |
| `setup-soldr:cargo-registry` | `setup-soldr-cargoregistry-v1-` | `resolve-setup.ts:1055` (v2 only if `cargoRegistryArchiveFormat()` resolves `"soldr-v2"`; v1 is what template-python-rust-cmd's main uses today) |
| `setup-soldr:cook` | `cook-base-v2-` | `cook-cache.ts:247` |
| `setup-soldr:cook-delta` | `cook-delta-v2-` | `cook-cache.ts:248` (retired fleet-wide, setup-soldr#533; declared only so a `[cache].retired` entry resolves to a real prefix) |
| `setup-soldr:cross-targets` | `setup-soldr-prepare-v3-` | `blessed-cross-prepare.ts:89` |
| `setup-soldr:dylint` | `setup-soldr-dylint-v2-` | `resolve-setup.ts:1185-1186` (one entry covers the dylint tool/driver/foundation together; v1 when `dylintModeEnabled` is false) |
| `setup-soldr:dylint-output` | `setup-soldr-dylint-output-v2-` | `resolve-setup.ts:1258` (v2 since setup-soldr v0.9.82; list the literal `setup-soldr-dylint-output-v1` in `[cache].retired` to delete the old generation) |
| `setup-soldr:soldr-mini` | `soldr-mini-v2-` | `soldr-mini-cache.ts:85` |
| `setup-soldr:solo-toolchain` | `solo-toolchain-v3-` | `solo-toolchain-cache.ts:276,280` (retired in the template, issue #6 D14; declared only so a `[cache].retired` entry resolves to a real prefix) |
| `setup-uv` | `setup-uv-2-` | astral-sh/setup-uv `src/cache/restore-cache.ts:12,105` -- `CACHE_VERSION = "2"`, `` `setup-uv-${CACHE_VERSION}-${getArch()}-${platform}-${osNameVersion}-${version}${pruned}${python}${cacheDependencyPathHash}${suffix}` ``. See "astral-sh/setup-uv's own cache" below. |
| `ci-lint` | `<family-id>-v1-` | this package (`ci_lint.cache.keys.build_family_key`); the round-4A brief writes this generically as `ci-lint:<family> -> <family>-v1-`, and `via = "ci-lint"` (the literal value schema-3 has always accepted, no colon) is treated as that same convention -- a round-4A decision, not a second `via` spelling |
| *(delta wrapper, not a `via` value)* | `delta-v1-pr-<N>-<family>-<platform>-b<base8>` | `ci_lint.cache.keys.build_delta_key` |

Round-4A added `setup-soldr:soldr-mini` and `setup-soldr:dylint` to the
allowlist and to `examples/rust-pypi-app/ci.toml`'s `[cache.family]`: the
live template repo saves `soldr-mini-v2-...` today, undeclared -- exactly
the "undeclared-but-saved family is `CACHE-001`" case the brief predicted
(reproduced in `ci_lint/tests/test_cache_audit.py`'s
`ClassifyLiveTemplateTest` before/after the two families were added).

#### astral-sh/setup-uv's own cache (round-4A amendment)

The live audit's first run flagged `setup-uv-2-...` as `CACHE-001`
(undeclared) on every push -- an owner decision then reclassified it as a
**real, declarable family**, not a permanent finding: `examples/rust-pypi
-app/ci.toml`'s `uv` family is now `via = "setup-uv"` (kept `max`/`per`
unchanged; dropped its old `key` array, since a non-`"ci-lint"` family's
real key is never ci-lint-built -- see `build_family_key`'s docstring,
which now refuses to append `.key` components onto an external prefix at
all, rather than silently fabricating a key shape that action never
produces).

After its fixed `setup-uv-2-` prefix, the key encodes (all from
astral-sh/setup-uv's own source): CPU arch (`getArch()`,
`src/utils/platforms.ts:18-31`, e.g. `x86_64`/`aarch64`) + a Rust-style
platform triple (`getPlatform()`:34-47, e.g. `unknown-linux-gnu` --
resolving musl vs glibc on Linux -- `apple-darwin`, `pc-windows-msvc`) + OS
name/version (`getOSNameVersion()`:86-103, e.g. `ubuntu-24.04`,
`macos-15`, `windows-2025`) + the resolved Python version + an optional
`-pruned` (the `prune-cache` input) + an optional `-py` (the `cache-python`
input) + a sha256 hash of every file the `cache-dependency-glob` input
matches (`restore-cache.ts:79-96`, via `hashFiles()` -- covers `uv.lock` by
that input's own default glob) + an optional user `cache-suffix`. This
means its live cardinality is per **(arch, platform, OS version, python
version)**, which can exceed a family declared `per = "os"` (3 groups) --
`template-python-rust-cmd` showed 6+ distinct `setup-uv-2-...` keys across
its 6 declared platforms on 2026-09-29. The amendment says "keep max/per",
so this is left as an open question for a future round rather than changed
here.

**The template must set its own `save-cache` input, not leave it at
`"auto"`.** Read from `astral-sh/setup-uv`'s `action.yml` (verified
2026-09-29): `enable-cache` defaults `"auto"` (caching on except for
`release`/tag-push/`pull_request_target`/`workflow_run` -- **`pull_request`
itself stays enabled**), and `save-cache` defaults `"auto"`, which "disables
saving for `merge_group` events" only -- a normal PR push is **not**
excluded. Left at its defaults, `astral-sh/setup-uv` would save on every
PR, exactly the base-layer-on-a-PR problem `CACHE-003` exists to catch (and
does: any `uv`-family entry the live audit finds on a `refs/pull/*` ref is
reported as `CACHE-003`, the identical rule a `setup-soldr:*` base layer
gets -- `ci_lint.cache.audit._check_cache_003` has no special case per
family, it fires for any non-delta, non-retired declared family's entry on
a non-default ref). The template's own `astral-sh/setup-uv` step must set
`save-cache` to a writer-flow-only condition (e.g. tied to the same ref
check a `cache = "write"` flow uses), not the bare `"auto"` default.

### `ci-lint cache key`

`ci-lint cache key <family> --repo . [--platform ID] [--pr N] [--base-key
K]` -- two distinct things depending on `--pr`:

- **Without `--pr`:** builds a `via = "ci-lint"` family's own base key from
  its declared `[cache.family.<id>].key` components (`"os"` ->
  `--platform`'s id, `"python"` -> `[python].pythons[0]`, anything else ->
  the first 16 hex chars of that repo-relative file's sha256), e.g. a
  hypothetical `mycache-v1-linux-x64-3.11-<16-hex>` for a
  `via = "ci-lint"` family declared `[cache.family.mycache]`. An external
  family (any `setup-soldr:*` shape, or `setup-uv`) has no `key` components
  to build from -- that action computes its real key itself, per issue #6
  §6's "two builders" rule -- so a `.key` array declared on one is ignored
  here (never appended onto its prefix, which would fabricate a key shape
  that action doesn't actually produce); `cache key` still resolves and
  returns its bare prefix, useful for e.g. `cache heal`'s exact-key
  deletes.
- **With `--pr N` (also needs `--platform` and `--base-key`):** builds the
  PR delta key `delta-v1-pr-<N>-<family>-<platform>-b<base8>`, where `base8`
  is the first 8 hex chars of `sha256(--base-key)` -- `--base-key` is the
  literal restored base cache's key string (whatever built it: setup-soldr
  or `ci-lint`), so a delta always self-heals the moment its base moves
  (issue #6 §6: "A delta whose `b<hash>` doesn't match the restored base is
  discarded").

Exit 2 on bad input (undeclared family, missing `--platform`/`--base-key`
with `--pr`, a missing key-component file).

### `ci-lint cache save-ok`

`ci-lint cache save-ok <family> --repo . --flow F --event E [--pr N]
[--fork] [--act] [--build-ok|--build-failed] [--exact-hit] [--new-units K]
[--payload-bytes B] [--base-key K --current-base-key K2]
[--lockfile-changed] [--tags "..."] [--rerun-saved] [--json]` evaluates
issue #6 §6's "when we do NOT save" table (`ci_lint.cache.save_ok
.evaluate_save_ok`), rule ID `CACHE-008`, **in order** -- the first rule
that applies wins:

| # | Fires when | CLI input |
| --- | --- | --- |
| 1 | fork or act | `--fork` / `--act` |
| 2 | a base-layer save (`--pr` absent) on a flow not in `[cache].write-on` | `--flow`, `[cache].write-on` |
| 3 | the family is in `[cache].retired` | (its own id) |
| 4 | the build step failed/cancelled (a test failure alone never blocks a save) | `--build-failed` |
| 5 | exact key hit, or fewer than the minimum new compile units (a fixed default of 1 -- ci.toml has no declared `-save-min-compiles` field) | `--exact-hit` / `--new-units` |
| 6 | payload below the family's `min`, or above its `max` | `--payload-bytes`, `[cache.family.<id>].min`/`.max` |
| 7 | (`--pr` given) the delta's restored base != the current base | `--base-key` != `--current-base-key` |
| 8 | (`--pr` given) the delta exceeds `[cache.pr].max-per-pr` | `--payload-bytes`, `[cache.pr].max-per-pr` |
| 9 | (`--pr` given) `--lockfile-changed` without a `[ci-cache-save]` tag | `--tags` |
| 10 | a re-run that already saved this exact key | `--rerun-saved` |

Prints `save: yes`, or `save: no (rule <n>: <reason>)`; exits 0 either way
(the reason is always in stdout/`--json`, never only inferred from the exit
code), exit 2 on bad input (family neither declared nor retired). Rule 8's
"...or the PR budget is still full after trimming closed PRs and evicting
by LRU" half needs live cache-account state across every open PR, which a
single `save-ok` call does not have -- that half is enforced by `cache
audit`/`ops` instead (a round-4A decision). `--tags` accepts either a raw
PR-title-shaped string (`"[ci-cache-save]"`) or a bare list
(`"ci-cache-save"`).

### `ci-lint cache audit` (live, read-only)

`ci-lint cache audit --repo . [--default-branch main] [--json]` --
`actions: read`, `GITHUB_TOKEN` + `GITHUB_REPOSITORY`. Lists every cache
(`GET /repos/{repo}/actions/caches`, paginated) and classifies each by
`[cache.family]` prefix (`ci_lint.cache.audit.classify`):

| Rule | Fires on |
| --- | --- |
| `CACHE-001` | a key matching no declared family prefix (and not a delta, not retired) |
| `CACHE-003` | a declared **base**-layer family's key saved on a ref other than `refs/heads/<--default-branch>` |
| `CACHE-005` | an entry <= 1KB (any family), or below its family's declared `min` |
| `CACHE-006` | >= 2 entries of the same family whose keys differ only in a trailing lockfile/version hash (best-effort: strips trailing hyphen-hex segments and groups what's left) -- all but the most-recently-accessed are flagged |
| `CACHE-008` | an entry of a closed/merged PR -- its key carries a delimited `pr-<N>` component (#23 §5; the legacy `delta-v1-pr<N>-...` form is still recognized) or it was saved on `refs/pull/<N>/merge` -- or a PR delta whose PR is closed/merged (one GraphQL query, all referenced PR numbers batched via aliased fields), or whose `b<base8>` matches none of the family's live base entries' own key hashes |
| `CACHE-009` | a key matching a `[cache].retired` prefix |
| `RUST-004` (round-6E) | a declared `via = "setup-soldr:dylint-output"`/`"setup-soldr:dylint"` family with NO live entry at all on `refs/heads/<--default-branch>`, while the default branch's latest run has a successful `dylint`-named job -- NOT "no entry created since that job's `started_at`": a live run against the real template repo proved that stricter freshness signal false-positives on an entirely healthy "exact hit, skipping save" case (an unrelated commit legitimately reproduces the identical `dylintOutputHash`), so only bare presence is trusted. Only evaluated when a `--fetch` is available (`cache audit`/`precheck --live` always have one; `ci_lint.cache.ops`'s trim/janitor/preprune classify+audit for their own rule subsets, none of which include RUST-004, and so never pass one -- zero extra live calls there) and only when the repo declares one of those two families; silent (no finding, not a pass) when no successful default-branch `dylint` job is found yet, so a repo that has simply never run Dylint successfully is never flagged |
| `CACHE-004` | (appended, not a live-only rule) total live bytes >= 90% of `[cache].budget` |

A PR delta's family/platform are parsed against `ci.toml`'s own declared
platform ids (`_split_family_platform`), not a hyphen-blind regex --
`compile`/`linux-x64` both legitimately contain hyphens, so a naive
`(?P<family>.+)-(?P<platform>[^-]+)-...` mis-splits them.

Exit 1 if any finding, 0 clean (`cache audit`'s own findings are all
`violation` status -- it is `precheck --live`, below, that downgrades all
but `CACHE-009` to a warning).

### `ci-lint cache save-check` (runtime, from `--log` evidence)

`ci-lint cache save-check --log <file> [--log <file> ...] --conclusion
<success|failure|...> [--run-url <url>] [--unusable-threshold 2] [--json]`
-- issue #7: `CACHE-010`'s static kill-switch check has no visibility into
whether a run actually saved anything, so these two rules read real job-log
evidence instead (`gh run view --job <id> --log`, or any captured stdout of
the same -- same "no live network assumed, inject `--log`" convention as
`ci-lint dylint coverage`/`ci-lint suite check`):

| Rule | Fires on |
| --- | --- |
| `CACHE-011` | `--conclusion` is exactly `success`, and the log shows a declared layer's save skipped for a reason other than an exact hit (`<layer>: ... skipping save`, `final <layer> session stats: missing`), or a `final ... summary: ...` line reporting `saved id=-1`. Never fires on a non-`success` conclusion -- a failed/cancelled build legitimately skips saving. |
| `CACHE-012` | `--log` files given OLDEST-FIRST show the SAME layer's restore reporting `produced an unusable payload: archive=<N>B extracted_files=0 extracted_bytes=0` on `--unusable-threshold` (default 2) or more CONSECUTIVE logs, with no successful save of that layer in between. |

Pure filesystem -- no `GITHUB_TOKEN`/`GITHUB_REPOSITORY` needed (unlike
`cache audit`'s live listing); the caller supplies the run's actual
conclusion since ci-lint cannot infer it from a log body alone. RED
evidence: zackees/clud `main` run `36463271709`, job `109067163056`
(`CACHE-011` -- conclusion `success`, both Dylint layers skip their save,
`saved id=-1`) and PR run `36467947289`'s restore (`CACHE-012` -- `archive=22B
extracted_files=0 extracted_bytes=0`, reproduced on repeated pushes since
the writer that should replace it is the same broken one `CACHE-011`
names). Exit 1 if any finding, 2 if a `--log` file is unreadable, 0 clean.

### `ci-lint cache trim` / `janitor` / `heal` / `preprune` (live, read-write)

All four need `actions: write`; `--dry-run` (or omitting a delete
function) never calls `DELETE` -- it only prints the plan.

| Command | Deletes | Extra output |
| --- | --- | --- |
| `cache trim [--max-deletes N] [--dry-run]` | `CACHE-008` entries only (closed/merged + stale-base PR deltas) | -- |
| `cache janitor [--dry-run] [--max-deletes N] [--stale-days D=5]` | (#23 §4) every entry of a closed/merged PR (by `pr-<N>` key component or `refs/pull/<N>/merge` ref; a failed PR lookup keeps them -- fail safe); `CACHE-001/003/005/006/009` entries (`CACHE-006` keeps the newest entry per key prefix); an `evict = "lru"` family's overflow beyond its footprint; anything not accessed in >= `D` days. **Safety:** never an entry created within `JANITOR_GRACE_SECONDS` (10 minutes), and never the newest base entry of a family/key shape that a required job restores (unless it is poisoned, `CACHE-005`, or retired, `CACHE-009`). | a before/after table (count, bytes per family label) |
| `cache heal --key K [--ref R] [--dry-run]` | exactly `key` (`DELETE .../actions/caches?key=<key>`, no id lookup) -- a writer flow's self-heal after an unusable restore | -- |
| `cache preprune --lockfile-changed [--max-deletes N] [--dry-run]` | the superseded (`CACHE-006`) entries of `lockfile = true` families, but only when the forecast is over budget | the forecast (same steady + lockfile-peak + PR-budget arithmetic as `CACHE-004`, but using each family's live max observed size where one exists, else its declared `max`) |

### `ci-lint cache budget` (live, read-only verdict)

The `cache-budget` job of `ci-pre.yml` (#23 §6), evaluated independently
of the janitor. `--event-name`/`--ref` default to `$GITHUB_EVENT_NAME`/
`$GITHUB_REF`; `--pr` defaults to the event payload's
`pull_request.number`.

| Context | Over `[cache].budget` | Exit |
| --- | --- | --- |
| `push` to the default branch, `schedule`, `workflow_dispatch` | hard failure | 1 |
| `pull_request` | warn-only, **except** the PR's own entries (`pr-<N>` key component or `refs/pull/<N>/merge`) exceed `[cache.pr].budget`, or the account would be under budget without them (its own additions caused the breach) -- then a failure | 0 / 1 |
| `push` to any other branch | warn-only | 0 |
| cache listing failed | warn-only (never fail on unread state) | 0 |

Never deletes anything unclassified, and never on a fork PR (the CLI
requires `GITHUB_TOKEN`/`GITHUB_REPOSITORY`, which a fork PR's default
token does not carry the scope for in the first place).

### `ci-lint cache delta manifest|pack|apply`

Pure filesystem (stdlib `tarfile`/`hashlib`/`json`), no network, no
`ci.toml` -- the PR-delta packing/unpacking mechanics behind issue #6 §6's
"PR caches: a small delta, never a base".

- `cache delta manifest --dir D --out M` -- a sorted `(relpath, size)` list
  of every file under `D`, plus a sha256 digest of that list (a size-based
  integrity check, not a full content hash -- cheap even over a large
  restored cache directory).
- `cache delta pack --dir D --base-manifest M --out T --family F --platform
  P --pr N` -- packs only the files under `D` absent from `M` or present
  with a different size into `T` (`.tar.gz`), whose first member is a small
  JSON header (`base_digest`, `family`, `platform`, `pr`).
- `cache delta apply --dir D --delta T --base-manifest M` -- verifies the
  header's `base_digest` equals `M`'s digest; on a mismatch, refuses with
  **exit 3** ("stale base, treat as miss" -- issue #6 §6's self-heal, the
  same property `cache key`'s `b<base8>` wrapper gives the key string
  itself). On a match, extracts every non-header member into `D`, overlaying
  whatever base restore is already there. Exit 2 on any other bad input
  (missing dir/manifest/delta file, malformed header).

### `ci-lint precheck --live`

`--live` (default off; the template's precheck turns it on) additionally
runs `cache audit` and folds its findings into the precheck report as
**warnings only** -- `CACHE-009` (a retired family actually present in the
live account) is the one exception, which still fails, matching issue #6
§9's precheck (live) row: "Warns only ... except CACHE-009". Missing
`GITHUB_TOKEN`/`GITHUB_REPOSITORY`, or a live-audit error, degrades to one
`needs_review` finding (never a crash, never a silent pass) -- the same
pattern round-3A's reuse lookup uses.

`--local` (without `--live`) keeps reporting `CACHE-005`/`006`/`008` as
explicitly "skipped (local)" `needs_review` findings (`ci_lint.precheck
.LOCAL_SKIPPED_CHECKS`) exactly as before -- `--live` is what actually
covers them now, so passing `--local --live` together drops those three
from the "skipped" list (their real findings appear instead) while leaving
`ACT-001` (the local act cache-store audit -- a different mechanism this
round did not build) as still explicitly skipped.

## `[local]`

| Field | Type | Meaning |
| --- | --- | --- |
| `runner` | string | e.g. `"bosn-act"`. Informational in round 1A. |
| `lanes` | array of strings | e.g. `["precheck", "fast", "dylint"]`. Informational in round 1A. |
| `cache` | string | e.g. `"machine"`. Informational in round 1A. |

## `[allow]`

| Field | Type | Meaning |
| --- | --- | --- |
| `workflows` | table of string -> array of strings | Every workflow filename this repository may have, mapped to its allowed triggers (`"pull_request"`, `"push:main"`, `"schedule"`, `"workflow_dispatch"`, `"workflow_call"`). `GEN-008`. |
| `actions` | array of strings | Allowed `owner/repo` action slugs. Every `uses:` (in a workflow or a composite action) must resolve to one of these, pinned to a 40-hex commit SHA. `SEC-004`. |
| `setup-soldr.only-in` | string | The one directory allowed to call `zackees/setup-soldr` directly (a composite-action wrapper). `CACHE-009`. |
| `setup-soldr.require` | table of string -> string | Inputs that wrapper's `zackees/setup-soldr` step must set, and to what value. A value of the special string `"plan"` (round-4B) means the actual input must instead be an expression derived from the precheck plan (contains `needs.precheck.outputs.`, or passes through an `inputs.*` value inside the composite) -- used for `save-cache`, so a save only fires when `plan.cache_mode == "write"`. `CACHE-009`. |
| `tools` | array of strings | Allowed bare tool names outside the `TOOL-001` banned list. Informational in round 1A. |
| `secrets` | array of strings | Allowed additional `secrets.*` references beyond `secrets.GITHUB_TOKEN`. Empty in the canonical example (OIDC-only, no repository secrets). |
| `platform-selector` | string | The one file allowed to contain a host `cfg`/`sys.platform` selector. `LAYOUT-001`. |
| `platform-code` | array of globs | Paths (globs; `**` supported) allowed to contain host selectors. `LAYOUT-001`. |
| `permissions` | table of string -> array of strings (round-4B) | Maps one non-free permission grant, written `"<permission>: <value>"` (e.g. `"actions: write"`), to the job ids allowed to hold it. A top-level `permissions:` block may hold at most `contents: read` + `actions: read` (free, no entry needed); any job may hold those two for free as well, but any wider grant must list that job's id here. `id-token: write` additionally always requires `environment: pypi` on the `publish` job specifically, allowlisted or not. `SEC-002`. |
| `cache-actions.only-in` | string (default `.github/actions/cache`) | The one directory allowed to call `actions/cache`/`actions/cache/save`/`actions/cache/restore` directly (a SHA-pinned composite-action wrapper). `CACHE-001`. Inside it, the wrapper's `key:` input must itself be a dynamic expression (a prior step's output, or an `inputs.*` passthrough), never a literal. `CACHE-002`. |
| `setup-uv.require` | table of string -> string (round-4B) | Inputs every `astral-sh/setup-uv` use (anywhere -- unlike setup-soldr, there is no single wrapper location) must set, and to what value; `"plan"` works exactly like `setup-soldr.require`'s special value, above. Used for `save-cache`; missing or a literal `"true"` is `CACHE-003` (both save unconditionally, including on PRs -- see "astral-sh/setup-uv's own cache" below). |

## `[publish]`

| Field | Type | Meaning |
| --- | --- | --- |
| `pypi.auth` | string | Must be `"oidc"` for the `rust-pypi-app` profile. |
| `pypi.environment` | string | e.g. `"pypi"`. The one job allowed `id-token: write` must be named `publish` and set this as its `environment`. `SEC-002`. |
| `pypi.mode` | string | `"mock"` in the template: proves the OIDC identity, never uploads. The planner maps a flow's `publish = "pypi"` through this value. |

## `[[exceptions]]`

An array of tables, one per declared exception.

| Field | Type | Meaning |
| --- | --- | --- |
| `rule` | string | The rule ID this exception covers, e.g. `"GEN-005"`. |
| `path` | string | The exact finding path this exception matches (e.g. `"ci.sh"`). Matching is `(rule, path)` equality. |
| `reason` | string | Free text; must justify the deviation, never "skip the check". |
| `issue` | string (URL) | The tracking issue for removing the exception. |
| `expires` | string (`YYYY-MM-DD`) | Once past, the exception no longer suppresses its finding (which reappears as a normal violation), and the expired exception itself becomes a violation: `CT-005`. |

A finding matching a non-expired exception is printed with status
`approved_exception` on every run (never silent); it does not count toward
precheck's exit code.

## Rule catalog

Every finding names its rule ID, a `path:line` when known, what is wrong,
and a concrete fix -- never "add it to the allowlist". A finding's status
is one of `violation`, `approved_exception`, or `needs_review` (a rule that
depends on parsed workflow YAML reports `needs_review`, not a silent pass,
when neither PyYAML nor `yq` is available).

| Rule | Checks | Fix direction |
| --- | --- | --- |
| `CT-001` | Unknown key anywhere in `ci.toml`. | Remove the key, or use the documented field name. |
| `CT-002` | Wrong TOML type, a missing required key, or a dangling/cyclic reference (flow/tag/suite/platform names, `[[exceptions]]` URL/date format). | Fix the value's type or the reference. |
| `CT-003` | `schema` is not `3`. | Set `schema = 3`. |
| `CT-004` | The workflow's checkout of `zackees/ci.yml` does not use the exact SHA in `ci.toml`'s `linter` field (or no such checkout exists). | Align the checkout `ref` (or `linter`) to the intended commit. |
| `CT-005` | A declared `[[exceptions]]` entry is past its `expires` date. | Fix the underlying finding and delete the entry, or renew it after review. |
| `CT-006` | `[platforms].*.target` does not match `Cargo.toml`'s `[workspace.metadata.soldr].targets` (or that table is missing). | Make the two target sets identical. |
| `TAG-001` | A PR-title bracket token looks reserved (`ci-`, `no-test`, `release` prefix) but is not a known tag. | Fix the spelling, or declare the tag in `[tags]`. |
| `TAG-002` | `[release]` combined with any `[no-test*]` tag. | Remove one or the other. |
| `TAG-003` | `on.pull_request.types` omits `edited`. | Add `edited` to the `types` list. |
| `GEN-001` | `.github/workflows/ci.yml` is missing or lacks `on.pull_request`. | Add it as the PR entry point. |
| `GEN-002` | A job with a non-Linux runner has no `if:`/`strategy.matrix` referencing `needs.precheck.outputs`. | Gate the job on the precheck plan's output. |
| `GEN-004` | (round-4B) The repo has Python sources but the `fast` job (directly in a `run:` line, or one level into a tracked `ci/*.py` script it calls) does not invoke `ruff check`, `ruff format --check`, and `pylint`; a standalone `black`/`isort` invocation is also `GEN-004`. An unresolvable script reference (missing file, parse error) is `needs_review`, never a silent pass. | Add the missing Ruff/Pylint invocation(s) to the `fast` job (directly or via a `ci/*.py` script); remove any Black/isort call. |
| `GEN-005` | A `run:` step longer than one line, shell control syntax, a banned `shell:`, or a tracked `.sh/.ps1/.bat/.cmd`/shebang script file. | Move the logic into a Python script, called as one line. |
| `GEN-008` | A workflow file (or one of its triggers) not declared in `[allow].workflows`, or more than one file declaring `pull_request`. | Declare it in `[allow].workflows`, or remove the trigger/file. |
| `GEN-014` | (#23 §1) A `ci-precheck.yml` workflow file, a `uses:` of one, or an `[allow].workflows` entry naming it. The fast first workflow is `ci-pre.yml`. | Rename it to `ci-pre.yml` (`git mv`), and update `ci.yml`'s `uses:` and `[allow].workflows`. |
| `GEN-015` | (#23 §3) A workflow-level `concurrency:` in `ci-pre.yml` (it would cancel a janitor sweep mid-delete). `WF-002`'s "every workflow has a top-level concurrency" does not apply to `ci-pre.yml`. | Delete it; concurrency in `ci-pre.yml` is per job, and the caller `ci.yml` may keep its own per-ref cancel group. |
| `GEN-016` | (#23 §3) A `ci-pre.yml` job other than `cache-janitor` with `concurrency:`, or with `needs: cache-janitor`. | Remove it: check jobs take no lock and never wait on a queued sweep. |
| `GEN-017` | (#23 §3) The `cache-janitor` job lacks the repo-wide, non-cancelling group. | `concurrency: { group: cache-janitor, cancel-in-progress: false }`. |
| `GEN-018` | (#23 §2) An install step in `ci-pre.yml`: `astral-sh/setup-uv`, `actions/setup-python`, or a `run:` invoking `uv`/`uvx`/`pip`/`pip3`/`pipx`/`python -m pip`. | Use stdlib `python3` only, or move the step into a later `ci.yml` job. |
| `SEC-001` | `secrets.<X>` other than `secrets.GITHUB_TOKEN`, or `secrets: inherit`. | Remove the dependency; this profile is OIDC-only. |
| `SEC-002` | Top-level permissions wider than `contents: read` + `actions: read` (round-4B: `actions: read` is now free at the top level too). Per job: any grant beyond `contents: read`/`actions: read` (free) whose job id is not listed in `[allow].permissions."<permission>: <value>"`; `id-token: write` additionally always requires `environment: pypi` on the `publish` job specifically. Checked identically inside a reusable (`workflow_call`) workflow file, by its own job ids. | Narrow the permissions block, or add the job id to `[allow].permissions` for that exact grant. |
| `SEC-003` | `pull_request_target` or `workflow_run` declared anywhere. | Use `pull_request` instead. |
| `SEC-004` | A `uses:` action not in `[allow].actions`, or not pinned to a 40-hex commit SHA. | Allowlist it and/or pin it by SHA. |
| `RUN-001` | A `runs-on` label outside the fleet list, or any `-latest` label. A `runs-on: ${{ matrix.<var>.runs_on }}` job whose `strategy.matrix.<var>` is built from a precheck plan platform-lanes output (`platform_lanes_json`/`platform_lanes_todo_json` -- these enumerate `[platforms]`) is resolved statically the same way: every `[platforms].*.runs-on` is checked, reported against `ci.toml` if bad. Any other dynamic `runs-on` expression is `needs_review`, not a violation. | Use a fleet label: `ubuntu-24.04`, `ubuntu-24.04-arm`, `windows-2025`, `windows-11-arm`, `macos-15`, `macos-15-intel`. |
| `RUN-002` | ci.yml#12: a cross-compiled job's display name must show where it builds and where it runs. Detected on the same platform-lanes matrix signal as `RUN-001`'s matrix case. A leg whose `runs-on` is a STATIC label (it always builds there, e.g. soldr cross-compiling on `ubuntu-24.04` for a Windows/macOS target) but whose `name:` has no `(on <host>...)` annotation is a violation; a leg whose `runs-on` is the dynamic `${{ matrix.<var>.runs_on }}` expression (it executes natively, compiling assumed impossible) but whose `name:` has no `(native...)` annotation is a violation. | Add a `(on <build-host>, <tool>)` annotation to the build leg's `name:` (e.g. `(on ubuntu-24.04, soldr)`) and a `(native...)` annotation to the exec leg's `name:`, so the Checks tab shows both sides of the split without opening the job. |
| `WF-001` | A job has no `timeout-minutes`. | Add one. |
| `WF-002` | A workflow has no top-level `concurrency` (except `ci-pre.yml`, where it is `GEN-015`). | Add one. |
| `WF-003` | `continue-on-error` anywhere. | Remove it; fix or gate the step instead. |
| `TOOL-001` | Bare `cargo`/`rustc`/`rustup`/`cargo-*`/`maturin`/`cross`/`cibuildwheel`/`pip`/`pipx`/`twine`/`curl`/`wget` as a command (in `run:` lines or `ci/*.py`/root `*.py` subprocess literals). | Wrap it through `soldr` or `uv`. |
| `TOOL-002` | A dependency-resolving `cargo` subcommand (`build`/`test`/`check`/`clippy`/`doc`/`run`/`nextest`) without `--locked`. | Add `--locked`. |
| `TOOL-003` | In a Rust repo (`pyproject.toml` with `[build-system]`, or `Cargo.toml` with `[workspace]`): a plain `uv run`/`uv sync` (in a `run:` line, `ci/**/*.py` subprocess argv, or a `uv run` shebang) missing `--no-project`/`--no-sync`/`--script`. | Add `--no-project`/`--no-sync`/`--script`, or set job-level `UV_NO_SYNC: "1"`. |
| `CACHE-009` | Static: `zackees/setup-soldr` used outside `[allow].setup-soldr.only-in`, missing/wrong `require` inputs (round-4B: a `require` value of `"plan"` means the actual input must be an expression derived from the precheck plan, not a literal), or an input enabling a retired cache family. Live (round-4A, `cache audit`/`precheck --live`): a live cache entry's key actually matches a `[cache].retired` prefix. | Call it only from the wrapper, with the required inputs; delete a live retired entry (`ci-lint cache janitor`). |
| `CACHE-010` (issue #7) | Static: a workflow/job/step env sets a kill-switch var (`ZCCACHE_DISABLE`, `SOLDR_NO_CACHE`, `SOLDR_CACHE_DISABLE`, `SOLDR_DYLINT_NO_CACHE`) truthy while `ci.toml` declares a `[cache.family]` via a `setup-soldr:*`/`zccache*` backend. | Remove the env var (or set it falsy); if intentional, retire the matching `[cache.family]` entry instead. |
| `CACHE-011` (issue #7) | Runtime, `ci-lint cache save-check --log ... --conclusion success`: a successful run's log shows a declared layer's save skipped for a non-exact-hit reason, or `saved id=-1`. | Inspect the writer job's cache-save gate for that layer (setup-soldr's success-marker identity check, or the invocation path that should write it). |
| `CACHE-012` (issue #7) | Runtime, `ci-lint cache save-check`: a layer's restore reports an unusable (0-extracted) payload on >= 2 consecutive `--log` files with no writer save in between. | Delete the poisoned entry (`ci-lint cache heal`) AND fix the writer flow (see the matching `CACHE-011` finding). |
| `RUST-002` (round-6E) | Dylint invoked from more than one job; a Dylint job whose `runs-on` doesn't resolve to Linux (`needs_review` for an unresolvable matrix/expression); bare `cargo dylint`/`cargo-dylint`/`dylint-link` (not `soldr dylint`/`soldr cargo dylint`) in a `run:` line or one level into a `ci/*.py` script (GEN-004's own follow-one-level convention, reused); `cargo install cargo-dylint`/`dylint-link`; `--workspace` without `--all` on any Dylint-related invocation. | Consolidate into one Linux `dylint` job; wrap every invocation through `soldr dylint`/`soldr cargo dylint`; drop the `cargo install`; add `--all`. |
| `LAYOUT-001` | A host selector (`cfg(...)`, `sys.platform`, ...) outside `[allow].platform-code`/`platform-selector`. | Move it behind the platform facade. |
| `RUST-005` | More integration-test targets exist than are declared in `[rust.tests].binaries`. | Declare each target, or consolidate `tests/*.rs`. |
| `RUST-011` | A private crate without `publish = false` / with `[features]` / with an optional dependency / with `cfg(feature)`; the public crate's `[features]` not of the form `x = ["dep:<private-crate>"]`; or `--all-features`/`cargo hack`/`--feature-powerset`/an out-of-`[rust].ship` `--features` value. | Fix the crate's manifest, or use only a `[rust].ship` feature set. |
| `RUST-012` | An undeclared test target, a declared-but-absent one, or an enabled `<crate>:lib` harness whose crate has zero `#[test]`. | Reconcile `[rust.tests].binaries` with the actual Cargo targets. |
| `RUST-013` | An exact `soldr==` pin in `pyproject.toml`'s `[build-system].requires`, or a literal `version:` input on a `zackees/setup-soldr` step, with no matching `[[exceptions]]` entry. | Float: `soldr>=<floor>` and no `version:` input; or add a `[[exceptions]]` entry recording the reason, owner, and bump path. |
| `PKG-003` | `[project.scripts]`/`[project.gui-scripts]` shadows `[python].cli.name`, or `[tool.soldr.pep517].bundle-bins` omits `[python].cli.crate`. | Remove the Python shim entry; add the crate to `bundle-bins`. |
| `PKG-004` | `pyproject.toml`'s build backend isn't `"soldr"`, `requires` has no `soldr` version requirement (`soldr>=<floor>` by default, or an exact `soldr==` pin per `RUST-013`), maturin appears in build requires/dependency-groups, or `uv.lock` has a non-soldr package depending on maturin. | Use `soldr` as the sole backend and maturin dependent. |
| `PKG-005` | A `try/except ImportError` around an import of `._native`. | Import it unconditionally so a missing native module fails loudly. |
| `CACHE-001` | Static: a raw `actions/cache` (or any of its `/save`/`/restore` sub-actions) used anywhere OTHER than the one sanctioned wrapper directory `[allow].cache-actions.only-in` (round-4B; default `.github/actions/cache`, still SHA-pinned per `SEC-004`). Live (round-4A): a cache entry whose key matches no declared `[cache.family]` prefix. | Move the call into the wrapper (or declare a family for a live undeclared entry, or stop writing it via `ci-lint cache janitor`). |
| `CACHE-002` | A volatile component (`github.sha`, `github.run_id`, `github.run_number`) in a `key:`/`cache-key-suffix:` input or a `[cache.family].key` entry. Round-4B: inside the sanctioned wrapper, a `key:` on the `actions/cache*` step that is a literal at all (even with no volatile token) -- it must be a step-output or `inputs.*` expression built by `ci_lint cache key`. | Key on `hashFiles(...)` or a date rotation instead; inside the wrapper, build the key from a prior step's output or an `inputs.*` passthrough. |
| `CACHE-003` | (round-4A, live) A declared **base**-layer family's cache entry was saved on a ref other than the default branch. (round-4B, static) `astral-sh/setup-uv`'s `save-cache` input, when `[allow].setup-uv.require` marks it `"plan"`-driven, is missing or a literal `"true"` -- both save unconditionally, including on PRs. | Delete the live entry (`ci-lint cache janitor`); check the writer job's ref condition and `save-ok` call site; set `save-cache` to a plan-derived expression. |
| `CACHE-004` | The proven worst-case cache footprint exceeds `[cache].budget`, or `[cache].budget` exceeds 10GB; (round-4A, live) total live bytes >= 90% of budget. `pre-prune = true` on every writer flow removes only the lockfile-change-peak term from the sum -- it does not waive the rest of the proof; `worst = steady + [cache.pr].budget` still must fit. | Lower family sizes/cardinality, raise the budget (up to 10GB), and/or set `pre-prune = true` on every writer flow (removes the lockfile-peak term only); live, run `ci-lint cache janitor`. |
| `CACHE-005` | (round-4A, live) A cache entry is poisoned: <= 1KB regardless of family, or below its family's declared `min`. | Delete the exact key (`ci-lint cache heal --key <key>`) so the next writer run repopulates it. |
| `CACHE-006` | (round-4A, live) >= 2 entries of the same declared family whose keys differ only in a trailing lockfile/version hash -- the family is superseded, not per-platform-distinct. | Delete the superseded (non-newest) entry (`ci-lint cache janitor`); disambiguate with `[cache.family.<id>].per` if more than one entry is legitimate. |
| `CACHE-008` | (round-4A, live; widened by #23 §4.1) Any entry of a closed/merged PR -- key with a delimited `pr-<N>` component, a legacy `delta-v1-pr<N>-...` key, or ref `refs/pull/<N>/merge` -- or a PR delta whose base hash matches none of that family's live base entries. A `pr-<N>`-keyed entry is PR-scoped, never a `CACHE-003` base layer. | Delete it (`ci-lint cache janitor` deletes closed-PR entries on every push sweep; `ci-lint cache trim` also covers stale-base deltas). |
| `CACHE-013` | (#23 §5, static) A cache save reachable from a PR (a workflow with `pull_request`/`workflow_call`, or a composite action) whose key input lacks a delimited `pr-<N>` component: `zackees/setup-soldr` `cache-key-suffix` (unless `save-cache: "false"`), `astral-sh/setup-uv` `cache-suffix` (unless `enable-cache: false` or `save-cache: false`), `actions/cache`/`actions/cache/save` `key`. Accepted: an expression containing `github.event.pull_request.number`, or the plan output `cache_key_pr` (`pr-<N>` on `pull_request`, empty elsewhere, from `ci-lint plan/precheck --github-output`); inside a composite action, an `inputs.*` passthrough. | Set the key input to `${{ needs.precheck.outputs.cache_key_pr }}` (or a `format('pr-{0}', github.event.pull_request.number)` expression). |
| `SEC-005` | (round-5, live, `ci-lint audit`) Any repository or `[publish].pypi.environment` environment Actions secret exists; a 403 (needs an admin token) is `needs_review`, never a pass. | This profile is OIDC-only (issue #6 §7): delete the stored secret(s). |
| `SEC-006` | (round-5, live, `ci-lint audit`) `[publish].pypi.environment` is missing, or its deployment branch policy doesn't restrict deploys to the default branch. | Create/restrict the environment's deployment branch policy to exactly the default branch. |
| `SEC-007` | (round-5, live, `ci-lint audit`) The repository's default Actions workflow permissions (`GET .../actions/permissions/workflow`) are not `"read"`. | Set "Read repository contents permission" (never "Read and write") in repo Settings -> Actions -> General. |
| `GEN-006` | (round-5, live, `ci-lint audit`) `required_status_checks.strict = true` on the default branch with no active merge-queue ruleset covering it (docs/case-studies/fbuild-ci-cost.md's candidate rule). | Configure a merge queue, or set `strict = false`. |
| `GEN-011` | (round-5, live, `ci-lint audit`) The default branch's required status checks don't include the gate check name (`--gate-check-name`, default `"CI OK"`); `needs_review` (not a violation) when branch protection is absent entirely -- a policy decision, not a code defect. | Add the gate check name to the branch's required status checks. |
| `PKG-006` | (round-5, `ci-lint release verify`) The staged release artifact set is missing/duplicates/has an extra wheel for a declared platform, is missing/duplicates the sdist, disagrees on version across artifacts, or (with `--smoke`) is missing/has a failing native-install smoke result for a staged wheel. | Stage exactly one wheel per declared platform plus one sdist, all at the same version, each passing its native install smoke. |

## The planner

`python3 -m ci_lint plan` computes `selection = (flow base ∪ tag adds) − tag
removes` for one event, and writes it as `plan.json` (see
`ci_lint.plan.Plan`): `flow`, `event_name`, `tags`, `platforms`
(`{id, target, runs_on, group}`), `suites`, `dylint_targets` (**every**
declared platform when `[lint.dylint].targets = "all-platforms"` -- Dylint
checks every declared target from one Linux job regardless of which
platforms this run actually builds, issue #6 §2/§3), `cache_mode`
(`"read"`/`"write"`), `publish` (`"none"`/`"rehearsal"`/`"mock"`, or
whatever `[publish.pypi].mode` resolves a flow's `publish = "pypi"` to),
`mergeable` (`false` if any tag removed a suite), a `digest` (sha256 of the
selection), `reasons` (why each lane was selected), `required_jobs` (the
job `id:`s `ci-lint gate` requires to succeed), and `needs_platform_lanes`
(`true` exactly when the selection includes any platform other than
`[flow.pr]`'s own default fast-lane platform(s) -- **not** simply "more
than one platform": `flow.release`/`flow.nightly`'s `platforms = "all"`
base is itself already beyond that fixed baseline, so they need the
platform matrix even though no tag added anything). Tags are read from the
PR title, and only for the `pull_request` event -- never for
`push`/`schedule`/`workflow_dispatch`.

### Platform-lane matrix outputs (round-3A)

Three more fields, computed alongside everything above (`ci_lint.plan.Plan`,
`ci_lint.plan.PlatformLane`):

| Field | Type | Meaning |
| --- | --- | --- |
| `platform_lanes` | array of `{id, target, runs_on, group, wheel, suites}` | Every selected platform **except** `[flow.pr]`'s own default fast-lane platform(s) (normally just `linux-x64`) -- one entry per non-default `platform-build`/`platform-run` matrix leg. `wheel` is `[platforms.<id>].wheel` or `null`. `suites` is `Plan.suites` (below) repeated per lane: every platform lane runs the same resolved suite selection the fast lane does (`unit`/`smoke` always, plus `integration`/`init`/`perf` when a flow or tag selected them, minus anything a tag removed) -- there is no separate per-platform suite composition to derive. Empty exactly when `needs_platform_lanes` is `false`. |
| `fast_suites` | array of strings | The suites for the `fast` (default-platform) lane -- identical to `Plan.suites`, exposed under its own name so the template can bind the `fast` job's suite input unambiguously alongside each platform lane's own `suites`. |
| `lane_digests` | object, lane key -> 12-hex-char string | A sha256 digest (truncated to 12 hex chars) of exactly the inputs that determine each lane's work, plus a shared "environment fingerprint" (`ci.toml`'s `linter` pin, and -- when `compute_plan` is given a `repo_root` -- the sha256 blob hashes of `Cargo.lock`, `uv.lock` and `rust-toolchain.toml`, each only if present). Keys: `"fast"` (fast-lane platform id(s)+target(s)+`fast_suites`), `"dylint"` (the sorted `dylint_targets`), and `"platform:<id>"` per platform lane (id+target+runs_on+suites+wheel). The template puts this digest in each job's display name, e.g. `fast [a1b2c3d4e5f6]`, `platform-run (windows-x64) [c1d2e3f4a5b6]` -- see "Title-edit reuse" below, which matches on exactly that bracketed substring. |

`--github-output` (both `ci_lint plan` and, from round-3A, `ci_lint
precheck --github-output`) adds `platform_lanes_json`, `fast_suites_json`
and `lane_digests_json` (each the field above, compact-JSON-encoded) on top
of the round-1A keys (`plan`, `platforms_json`, `cross_platforms_json`,
`suites_json`, `dylint_targets_json`, `needs_platform_lanes`,
`mergeable`). Round-4B adds one more: `cache_save`, `"true"`/`"false"` =
`(plan.cache_mode == "write")` -- a writer-flow step (`astral-sh/setup-uv`'s
`save-cache`, or a `setup-soldr` wrapper's `save-cache` passthrough) binds
straight to `needs.precheck.outputs.cache_save`, satisfying
`[allow].setup-uv.require`/`[allow].setup-soldr.require`'s `"plan"` value.

## Title-edit reuse (round-3A)

`ci_lint plan --reuse` (and `ci_lint precheck --reuse`, which computes the
same thing alongside its `--plan-out`/`--github-output`) looks up whether
any lane in this run's `lane_digests` already has a **proven-green** job
for the **identical digest** on the **identical PR head SHA**, so a title
edit that only adds or removes tags never re-runs work that is still
valid.

**Security note:** reuse only ever *skips* work already proven green for
the identical digest on the identical head SHA. It is recomputed fresh on
every `plan --reuse` invocation (never read from a cached/stale file), it
is scoped by the GitHub API's own `head_sha=` filter, and (see "Reuse
verification" below) `ci_lint gate --reuse` re-verifies it live before
ever treating a skipped required job as a pass. A lane whose inputs
changed gets a different digest and is never matched against an older
job's name; a different commit's jobs are excluded by the `head_sha=`
query itself.

Only consulted for the `pull_request` event, and only when `GITHUB_TOKEN`
and `GITHUB_REPOSITORY` are both set and the event JSON carries a PR head
SHA -- `push`/`schedule`/`workflow_dispatch` and any missing precondition
always produce "reuse nothing" with no network call (`ci_lint.reuse`'s
`empty_result`). On any GitHub API error, the result is the same "reuse
nothing", plus a warning printed to stderr -- reuse computation never
fails the precheck.

**Lookup (`ci_lint.reuse.compute_reuse`, stdlib `urllib`, injectable
`fetch` in tests -- fixtures under
`ci_lint/tests/fixtures/runtime/reuse/`):** `GET
/repos/{repo}/actions/runs?head_sha=<PR head sha>&event=pull_request&per_page=50`,
keep only runs whose `path` ends with `.github/workflows/ci.yml`, newest
first, excluding the current run (`GITHUB_RUN_ID`); for each such run `GET
/repos/{repo}/actions/runs/{id}/jobs?per_page=100`. A lane is reusable
when some previous job has `conclusion == "success"` and its `name`
contains the exact bracketed digest (`[<digest>]`) for that lane.

**Output**, added to `plan.json`'s `"reuse"` key and to `--github-output`:

| Key | Type | Meaning |
| --- | --- | --- |
| `reuse_json` | object, lane key -> `{run_id, job_id, html_url}` or `null` | Every lane in `lane_digests`, mapped to the reused job found (or `null`). |
| `platform_lanes_todo_json` | array, same shape as `platform_lanes` | `platform_lanes` minus every platform lane whose `"platform:<id>"` key in `reuse_json` is non-null -- the platform legs the matrix job(s) still actually need to run. |
| `fast_reused` | bool | `reuse_json["fast"]` is non-null. |
| `dylint_reused` | bool | `reuse_json["dylint"]` is non-null. |

### `required_jobs` / the gate job-id convention

`ci_lint.plan._required_job_ids` is the one place this convention is
defined; the template's `ci.yml` must name its jobs to match:

| Job `id:` | Always required? | What it covers |
| --- | --- | --- |
| `precheck` | always | the precheck job itself |
| `fast` | always | the default `linux-x64` build+unit+smoke lane |
| `dylint` | when `dylint_targets` is non-empty | the one Linux Dylint job (all declared targets) |
| `platform-build` | when `needs_platform_lanes` | the Linux cross-build matrix job -- GitHub aggregates every matrix leg into one `needs.platform-build.result` |
| `platform-run` | when `needs_platform_lanes` | the native-runner execute matrix job -- same aggregation |
| `init` | when the `init` suite is selected | the from-zero job: no cache restore, template instantiation, precheck + build + smoke of the generated repo |
| `perf` | when the `perf` suite is selected | the perf job; it must succeed even when the suite is non-gating (gating only decides whether a regression fails it) |
| `release-verify` | when `publish` is `rehearsal` or `mock` | staged-artifact completeness (`ci-lint release verify`) |
| `publish` | when `publish` is `mock` | the top-level OIDC mock publisher (PyPI can't trust a reusable workflow, warehouse#11096) |

`ci-ok` (the gate job itself) is **never** in `required_jobs` -- a job
cannot require its own result.

| Event | Base flow |
| --- | --- |
| `pull_request` | `pr` |
| `push` (to the default branch) | `main` |
| `schedule` | `nightly` |
| `workflow_dispatch` | `release` (requires a 40-hex `sha` input, recorded as `dispatch_sha`) |

`--github-output` (writes to `$GITHUB_OUTPUT`) adds: `plan` (the compact
JSON), `platforms_json`, `cross_platforms_json`, `suites_json`,
`dylint_targets_json`, `needs_platform_lanes`, `mergeable`.

## Runtime commands (round-2A)

These run *after* a build/wheel/install, unlike the precheck rule groups
above, which are all static.

| Command | Reads | Checks | Exit |
| --- | --- | --- | --- |
| `ci-lint units --repo <path> --artifacts <file.jsonl> [--json]` | `cargo ... --message-format=json` lines (`ci_lint.cargo_messages`; non-artifact lines ignored) | `RUST-011`: a private crate ([rust].private) compiled with more than one distinct feature set in the same profile; the public crate ([rust].public) compiled with a feature set not in `[rust].ship`. Prints a table per crate of every distinct `(features, profile, target kind, target triple)` combination actually compiled -- the target triple is read from the build output path, matched against `[platforms].*.target`, else `"host"`. | 1 if any violation |
| `ci-lint tests size --repo <path> --artifacts <file.jsonl>` | same artifacts file, `profile.test == true` records with an `executable` | `RUST-012`: an undeclared test binary, a declared one never produced, one over `[rust.tests].max-binary`, or the sum over `[rust.tests].max-total`. Names map `<crate>:lib` / `<crate>:bin:<name>` / `<crate>:test:<name>`. | 1 if any violation |
| `ci-lint wheel check --repo <path> --wheel <path.whl> [--sdist <path.tar.gz>]` | the wheel's own bytes (stdlib `zipfile`) | `PKG-003`: `<dist>.data/scripts/<cli>[.exe]` exists, has no `#!` shebang, its magic bytes match the wheel's platform tag (ELF/PE/Mach-O + machine/cputype), and no `console_scripts` entry shadows it. `PKG-004`: `*.dist-info/WHEEL`'s `Generator` is reported (`needs_review`, not a violation, if it doesn't mention soldr); the abi tag matches `[python].abi3`; with `--sdist`, its bundled `pyproject.toml` also declares `build-backend = "soldr"`. `PKG-005`: a `_native` extension file exists in the wheel. | 1 if any violation |
| `ci-lint wheel installed --repo <path> --venv <dir>` | the venv's `bin`/`Scripts` dir + a subprocess run of the installed CLI and the venv's `python` | Same magic-byte check against the current host; `<cli> --version` exits 0 (`PKG-003`); `<venv python> -c "import <pkg>, <pkg>._native as n"` succeeds and `n.__file__` ends in a compiled-extension suffix (`PKG-005`). `<pkg>` is `pyproject.toml`'s `[project].name` with `-` replaced by `_`. | 1 if any violation |
| `ci-lint gate --repo <path> --plan <plan.json> --needs <needs.json> [--reuse <reuse.json>] [--event <event.json>]` | `ci-lint plan`'s own JSON output + GitHub's `toJSON(needs)` verbatim, plus (round-3A) the plan's reuse map and the PR event JSON | The `CI OK` aggregator: every job in `plan.required_jobs` must have `needs[job].result == "success"`, or be a **verified reuse** (see "Reuse verification" below) when `skipped`. A `skipped` job that is neither is `TEST-001`; a job id absent from `needs` entirely is `needs_review`, never a silent pass. If `plan.mergeable` is `false`, prints `not mergeable: tag(s) <tags> remove required coverage` to stderr regardless of job results. | 1 if not green |
| `ci-lint suite check --repo <path> --suite <id> [--cargo-test-log <file>...] [--pytest-junit <file>...]` (round-6C) | Every `--cargo-test-log` file, scanned for libtest summary lines (`test result: <outcome>. N passed; M failed; K ignored; ...`, one per test binary -- a merged-doctest binary's own line is summed in like any other); every `--pytest-junit` file, parsed as JUnit XML (stdlib `xml.etree.ElementTree`, `<testsuite>`/`<testsuites>` `tests`/`failures`/`errors`/`skipped` attributes) | `TEST-001`: `[suites.<id>].required = true` and at least one test was ignored/skipped across all sources. `TEST-002`: `[suites.<id>].required = true` and zero tests were executed (passed + failed) across all sources -- including when no `--cargo-test-log`/`--pytest-junit` files were given at all. A non-required suite only prints per-source and total counts; neither rule fires for it. An unknown `--suite` id, or a file that isn't a recognizable libtest log / JUnit XML, is an error (exit 2), never silently "zero tests". | 1 if any violation, 2 on a bad `--suite`/file |
| `ci-lint dylint coverage --repo <path> [--log <file>...] [--json]` (round-6E) | Each `--log`, auto-detected as one of two real shapes: `ci/dylint.py --results-out`'s own `DylintPassResult[]` JSON (`{shape, targets, command, seconds, returncode}` -- `targets` already names every triple, host included, one invocation covered), or a raw GitHub Actions job log (`gh run view --job <id> --log`), regex-matched for the `+ soldr dylint ...`/`+ soldr cargo dylint ...` invocation trace line's `--target` flags, rustc's `` Checking with toolchain `...` `` banner (names the HOST triple as a suffix), a `Finished` completion marker printed AFTER that banner (one printed before it is the lint-library's own build, not the workspace check), and an `` error[E0463]: can't find crate for `core` `` + its `` the `<target>` target may not be installed `` note | `RUST-003`: every `ci.toml [platforms]` id in `ci_lint.plan.dylint_target_platform_ids(ci)` (declared when `[lint.dylint].targets = "all-platforms"`) must have its target triple proven covered by a completed pass in the combined evidence from every `--log` given; a multi-target invocation's several `--target` flags (or one `DylintPassResult.targets` entry) each count toward their own platform. An `E0463` failure is reported by name (`"provision prebuilt target std via 'soldr dylint prepare --target T'"`), in addition to (not instead of) the generic "not covered" finding for every OTHER target that invocation named but never proved finished. No `--log` given flags every declared target. A missing/malformed `--log` file is an error (exit 2), never silently "zero coverage". | 1 if any violation, 2 on an unreadable/malformed `--log` |
| `ci-lint example drift --example <path> --repo <path>` (round-6C) | Both ci.toml files, parsed with stdlib `tomllib` as raw tables (never through the typed schema loader, which would normalize away exactly the key-presence gaps this needs to catch) | Issue #6's round rule, "the ci.toml example is identical in both repos apart from repo-specific values," made machine-checked: every differing key is repo-specific (allowed) or schema/policy drift. See "example drift classification" below for the exact allowed list. | 1 if any schema/policy drift, 2 if a file can't be read/parsed |
| `ci-lint precheck --local` (or env `ACT=true`) [`--github-output`] [`--reuse`] | -- | Runs every static group as usual, then adds one `needs_review` finding per check that needs the GitHub API and is not implemented yet (`CACHE-005`/`006`/`008`, `ACT-001`), each explicitly labeled `skipped (local): ...` -- never silently passed. Round-3A: `--github-output` writes the same `$GITHUB_OUTPUT` keys as `ci-lint plan --github-output` (computing a plan internally, same as `--plan-out` already did); `--reuse` adds the title-edit reuse lookup to both `--plan-out`'s written JSON (under a `"reuse"` key) and `--github-output`. | as `precheck` |
| `ci-lint selftest` | -- | Runs this package's own `unittest` suite (`ci_lint.selftest`; zackees/zccache#1760 -- agents must be able to run this directly). | 0/1 |

### Reuse verification (round-3A, `ci-lint gate --reuse`)

A required job whose `needs[job].result == "skipped"` is treated as
**success** iff:

1. `--reuse <reuse.json>` (the plan's own `reuse_json`/`"reuse"` map) marks
   the job's lane(s) as reused, **and**
2. when a GitHub token is available (env `GITHUB_TOKEN` + `GITHUB_REPOSITORY`
   both set), the referenced job is **re-fetched live**
   (`GET /repos/{repo}/actions/jobs/{job_id}`) and confirmed
   `conclusion == "success"`, its `name` contains the exact bracketed
   digest (`plan.lane_digests[lane]`) for that lane, and -- when
   `--event <event.json>` supplies the current run's PR head SHA -- the
   referenced job's own `head_sha` matches it.

`fast` and `dylint` map to the reuse map's `"fast"`/`"dylint"` keys 1:1.
`platform-build` and `platform-run` are matrix aggregates (GitHub folds
every leg of a matrix job into one `needs.<id>.result`), so they verify as
reused only when **every** currently-selected `"platform:<id>"` lane (read
from `plan.lane_digests`) is itself reused -- exactly the condition under
which `platform_lanes_todo_json` is empty.

**Without a token**, a job the plan marks reused but cannot be live-verified
is `needs_review` (`ci-lint gate` still exits non-zero) -- never a silent
pass. A job the plan does *not* mark reused, or whose live verification
fails (wrong digest, wrong head SHA, not `success`), is the pre-existing
`TEST-001` "a skip is a failure" outcome. The gate's summary table shows
`reused from run <N>` next to any job whose skip was accepted this way.

### Example drift classification (round-6C, `ci-lint example drift`)

`ci_lint.example_drift` walks both parsed `ci.toml` tables key by key and
classifies every difference (missing key, extra key, or a differing
value) as one of:

- **repo-specific (allowed)**: the top-level `linter` pin; each
  `[platforms.<id>]`'s `runs-on` and `wheel`; `[rust].public`,
  `[rust].private`, `[rust].ship` and `[rust.tests].binaries`;
  `[allow].platform-selector` and `[allow].platform-code`; each
  `[suites.<id>]`'s `run` command (its own test-invocation path -- but
  **not** `required`/`gating`/`cache`/`kind`, which are policy, not a
  path); each `[cache.family.<name>]`'s `max` and `min`; the whole
  `[[exceptions]]` array; `[python].cli`'s `name` and `crate`.
- **schema/policy drift (must match)**: everything else -- a missing or
  extra table/key at any level (including a whole extra platform, suite,
  cache family, flow or tag the example doesn't declare), a different
  enum or value (`[lint.dylint].shape`/`.budget`, `[suites.<id>]`'s
  `required`/`gating`/`kind`, `[cache.family.<name>]`'s `via`/`lockfile`/
  `per`, `[flow.*]`, `[tags.*]`, `[allow].permissions`, `[publish]`, ...).

This is checked, not designed here: run `ci-lint example drift` against a
candidate repository and read its report. If a repository's `ci.toml`
disagrees with the example outside the allowed list, either the example
needs the same fix (the repository found something the example got
wrong) or the repository does (it drifted) -- the report doesn't decide
which; that's a human/orchestrator call, recorded back into this file and
`examples/rust-pypi-app/ci.toml` once made.

Every command above is stdlib-only (`zipfile`, `tarfile`, `subprocess`,
`tomllib`, `json`, `xml.etree.ElementTree`) and every finding is a
`ci_lint.finding.Finding` (`--json` prints the same fields as
`precheck --json`'s `findings` array).

A `[tags.<id>]` entry with `flow` set switches the base flow itself (e.g.
`[release]` on a PR runs the `release` flow as a rehearsal, with
`publish = "rehearsal"`).

## Settings audit (round-5)

`ci-lint audit --repo . [--default-branch main] [--gate-check-name "CI OK"]
[--json]` -- live, read-only, `GITHUB_TOKEN` + `GITHUB_REPOSITORY`
(`ci_lint.settings_audit`, injectable `FetchStatusFn` --
`ci_lint.github_api.default_fetch_status`, which returns `(status_code,
body)` instead of raising, so a 403 ("needs an admin token") and a 404
("legitimately doesn't exist") are never collapsed into one generic
error). Distinct from `ci_lint cache audit` (which classifies GitHub
Actions cache entries) -- this module never touches the cache API.

| Rule | Checks | On 403 | On 404 |
| --- | --- | --- | --- |
| `SEC-005` | Any repository Actions secret (`GET .../actions/secrets`) or `[publish].pypi.environment`'s environment secret (`GET .../environments/<name>/secrets`) exists. | `needs_review` ("requires an admin token") -- **never** a pass. | not applicable (no secrets to report). |
| `SEC-006` | `[publish].pypi.environment` is missing (`GET .../environments/<name>`), or its `deployment_branch_policy` doesn't restrict deploys to `--default-branch` (`protected_branches`, or a `custom_branch_policies` pattern list equal to exactly `[--default-branch]`, checked via `GET .../environments/<name>/deployment-branch-policies`). | `needs_review`. | violation ("does not exist"). |
| `SEC-007` | `GET .../actions/permissions/workflow`'s `default_workflow_permissions` is not `"read"`. | `needs_review`. | n/a (this endpoint always exists for a real repo). |
| `GEN-006` | Classic branch protection's `required_status_checks.strict` is `true` with no **active** `merge_queue` ruleset (`GET .../rulesets` + per-id `GET .../rulesets/<id>`) covering `--default-branch` (best-effort `conditions.ref_name.include` match: `~ALL`/`~DEFAULT_BRANCH`/the literal `refs/heads/<branch>`). docs/case-studies/fbuild-ci-cost.md's candidate rule. | `needs_review`. | **treated as none** -- no branch protection means nothing for GEN-006 to flag (GEN-011 covers the "no protection at all" case). |
| `GEN-011` | The default branch's required status checks (`required_status_checks.contexts` + the newer `.checks[].context`) don't include `--gate-check-name` (default `"CI OK"`). | `needs_review`. | `needs_review` ("a policy decision, not a code defect") -- **not** a violation. |

Every check is independent (one missing/forbidden endpoint never blocks
another). A transport failure (DNS, timeout -- `GitHubApiError`) on any
single call degrades that one check to `needs_review`, never a crash and
never a silent pass. Prints a per-rule summary table, then every finding;
exits 1 if any finding is `violation` (not on `needs_review` alone --
matching the rest of ci_lint's convention that only a `--live`
network-dependent check downgrades an inconclusive result rather than
failing on it).

## Publish (round-5)

`ci-lint publish oidc-check --repo . --audience <aud> [--expect-ref
refs/heads/main] [--expect-event workflow_dispatch] [--json]` --
`ci_lint.publish_oidc`, issue #6 §7's mock publisher. Refuses to run at all
(**exit 2**, before any network call) unless `ci.toml`'s
`[publish].pypi.mode == "mock"`.

Needs `ACTIONS_ID_TOKEN_REQUEST_URL` + `ACTIONS_ID_TOKEN_REQUEST_TOKEN`
(set only when the job has `permissions: id-token: write`) and
`GITHUB_REPOSITORY`. Requests the token from `ACTIONS_ID_TOKEN_REQUEST_URL
+ "&audience=" + <aud>`, authenticating with
`ACTIONS_ID_TOKEN_REQUEST_TOKEN` (`ci_lint.publish_oidc
.request_oidc_token`, through the same injectable `FetchFn` as everywhere
else in this package). Decodes **only** the JWT's middle (payload)
segment, base64url, in memory (`decode_payload` -- the header and
signature segments are never read; this module does not and cannot verify
the signature itself, matching how `actions/toolkit`'s own
`core.getIDToken` trusts the Actions runtime's authenticated channel).

Asserts exactly these claims (`assert_claims`):

| Claim | Expected |
| --- | --- |
| `repository` | `GITHUB_REPOSITORY` |
| `ref` | `--expect-ref` |
| `environment` | `ci.toml`'s `[publish].pypi.environment` |
| `event_name` | `--expect-event` |
| `workflow_ref` | `"<repository>/.github/workflows/ci.yml@<expect-ref>"` -- asserting the **full** shape (not just a suffix) is what proves the workflow is a top-level, non-reusable one, per PyPI's trusted-publishing requirement (warehouse#11096). |
| `aud` | `--audience` |

Prints only the claim **name** and PASS/FAIL for every assertion; the
**value** is additionally printed only for the five claims above named
`repository`/`ref`/`environment`/`event_name`/`workflow_ref` (never `aud`,
and never anything else) -- `ci_lint.publish_oidc.PRINTABLE_CLAIMS`. The
raw token, its `jti`, and its signature segment are **never** printed,
logged, or included in `--json` output, by construction: the token string
is returned exactly once (from `request_oidc_token` to its one caller) and
never touches a print/log/exception-message call anywhere in this module
or the CLI layer (`ci_lint/tests/test_publish_oidc.py`'s
`NeverPrintsTokenTest` asserts this, including one test that drives the
actual CLI command end-to-end with a fake token and greps its captured
stdout+stderr). Then prints `mock publish: stopping before upload` and
exits 0 if every assertion passed, else 1.

## Release verify (round-5)

`ci-lint release verify --repo . --dist <dir> --sha <candidate sha>
[--smoke <dir>] [--json]` -- `ci_lint.release`, issue #6 §3/ci.yml#4's
release-candidate gate. Pure filesystem (stdlib `zipfile`/`tarfile`/
`hashlib`, via the reused `ci_lint.runtime.wheel.parse_wheel_filename`/
`check_wheel`) -- no network, no subprocess.

For every `[platforms.<id>]` entry, `expected_wheel_tag_pattern` derives
the wheel `platform_tag` regex its wheel must match, from the platform's
`group` and its `target` triple's CPU architecture:

| `[platforms.<id>].group` | Expected `platform_tag` |
| --- | --- |
| `linux` | `<[platforms.<id>].wheel or "manylinux_2_17">_<x86_64\|aarch64>` |
| `windows` | `win_amd64` (x86_64) / `win_arm64` (aarch64) |
| `macos` | `macosx_*_arm64` (aarch64) / `macosx_*_x86_64` (x86_64) -- the macOS deployment-target prefix is a wildcard |

`--dist` must contain exactly one wheel matching each declared platform's
pattern, plus exactly one sdist (`{name}-{version}.tar.gz`), every staged
artifact (wheel or sdist) agreeing on the same `{version}`, and abi3
`cp310` per `[python].abi3` (checked by the reused `wheel check`, not
re-implemented). `PKG-006` covers: a platform with zero matching wheels
(missing), more than one (duplicate), a wheel matching no declared
platform (extra), an unparseable `.whl`/`.tar.gz` filename, a missing or
duplicated sdist, and a version disagreement across the staged set.
`check_wheel`'s own `PKG-003`/`004`/`005` findings are folded straight
into the report (reused, not duplicated) -- the sdist backend check
(`PKG-004`) runs once, paired with the first staged wheel.

`--smoke <dir>` (optional) consumes an already-written
`smoke-results/*.json` directory (each file `{"platform": ..., "wheel":
"<the staged wheel's exact filename>", "passed": bool, "detail":
"..."}`) -- the per-wheel **native install smoke** itself runs elsewhere
(the platform-run job, on that platform's own native runner); this command
only requires a passing result per staged wheel (`PKG-006`: missing or
failing).

Always writes `<dist>/release-manifest.json` (schema-versioned): the
candidate SHA, `ci.toml`'s own sha256 digest, the resolved version, and
one `{path, kind, platform, version, sha256}` record per staged artifact
(`sha256` computed from the artifact's own bytes, streamed, never trusted
from a filename or a build log). Exits 1 if any finding is `violation`
(any rule, not just `PKG-006`), else 0; exits 2 on bad input (`--dist` not
a directory, `--sha` not a 40-hex commit SHA).

## Perf compare (round-5)

`ci-lint perf compare --repo . --baseline <b.json> --current <c.json>
[--threshold-pct N] [--json]` -- `ci_lint.perf`. AGENTS.md's typed-
benchmark rule applies directly: `BenchmarkRecord` (`name`, `unit`,
`samples: tuple[float, ...]`, `median`) and `BenchmarkFile`
(`schema_version`, `benchmarks`) are frozen dataclasses; `load_benchmark_
file` is the one place the JSON wire boundary (typed as `JsonRecord =
dict[str, JsonScalar | list[float]]`, never `dict[str, Any]`) is read,
validated field-by-field, and converted -- nothing past that function ever
sees a raw dict again. The file format:

```json
{
  "schema_version": 1,
  "benchmarks": [
    {"name": "build_time", "unit": "s", "samples": [1.21, 1.19, 1.25], "median": 1.21}
  ]
}
```

`compare` reports every benchmark present in either file: a `delta_pct`
(`None` for a benchmark new in `--current`, removed since `--baseline`, or
whose baseline median is exactly `0`), and `regression` (only ever `true`
when `--threshold-pct` is given **and** `delta_pct` exceeds it). **Non-
gating unless BOTH** `ci.toml`'s `[suites.perf].gating = true` **and**
`--threshold-pct` is supplied (`is_gating`) -- every delta is always
printed either way; the command only exits 1 (instead of 0) when gating is
active and at least one benchmark regressed past the threshold. The
printed/`--json` report itself carries a `schema_version` field
(`PERF_SCHEMA_VERSION`).
