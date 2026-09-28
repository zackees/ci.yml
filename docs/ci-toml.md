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

`cache save-ok/heal/trim/janitor/budget` and `audit` are still later
rounds; `python3 -m ci_lint cache|audit ...` currently exits 2 with "not
implemented yet" (see `ci_lint/runtime_stub.py`).

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
| `shape` | string | e.g. `"measure"`. Round-2 scope (multi-target vs. sequential vs. per-target-jobs); not enforced in round 1A. |
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
| `via` | string | e.g. `"setup-soldr:build-cache"` or `"ci-lint"`. Informational in round 1A. |
| `max` | string (size) | The per-instance cap, used directly in `CACHE-004`'s arithmetic. |
| `lockfile` | bool (default `false`) | If true, this family's steady-state size is counted **twice** in `CACHE-004` (steady state, plus the lockfile-change peak where old and new coexist). |
| `per` | string (optional) | Cardinality multiplier. A **strict enum**: `"none"` (the default, same as omitting it) -> `1`; `"platform"` -> number of declared platforms; `"cross-platform"` -> platforms minus one; `"os"` -> number of distinct platform groups. Any other value (e.g. `"target"`) is `CT-002` -- never a silent fallback to `1`, which would undercount `CACHE-004`'s arithmetic without warning. |
| `min` | string (size, optional) | Informational in round 1A. |
| `key` | array of strings (optional) | Key components, e.g. `["os", "python", "uv.lock"]`. Scanned by `CACHE-002` for volatile tokens the same way a workflow's `with.key` is. |

`CACHE-004`'s formula: `worst = Σ(family.max × cardinality) + Σ(family.max × cardinality, families with lockfile=true) + [cache.pr].budget` -- unless **every** writer flow (`cache = "write"`) sets `pre-prune = true`, in which case the middle (lockfile-change-peak) term is dropped: `worst = Σ(family.max × cardinality) + [cache.pr].budget`. Either way, `worst > [cache].budget` is always a violation; pre-prune narrows the sum, it never waives the comparison. Precheck always prints the arithmetic and which formula it applied.

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
| `setup-soldr.require` | table of string -> string | Inputs that wrapper's `zackees/setup-soldr` step must set, and to what value. `CACHE-009`. |
| `tools` | array of strings | Allowed bare tool names outside the `TOOL-001` banned list. Informational in round 1A. |
| `secrets` | array of strings | Allowed additional `secrets.*` references beyond `secrets.GITHUB_TOKEN`. Empty in the canonical example (OIDC-only, no repository secrets). |
| `platform-selector` | string | The one file allowed to contain a host `cfg`/`sys.platform` selector. `LAYOUT-001`. |
| `platform-code` | array of globs | Paths (globs; `**` supported) allowed to contain host selectors. `LAYOUT-001`. |

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
| `GEN-005` | A `run:` step longer than one line, shell control syntax, a banned `shell:`, or a tracked `.sh/.ps1/.bat/.cmd`/shebang script file. | Move the logic into a Python script, called as one line. |
| `GEN-008` | A workflow file (or one of its triggers) not declared in `[allow].workflows`, or more than one file declaring `pull_request`. | Declare it in `[allow].workflows`, or remove the trigger/file. |
| `SEC-001` | `secrets.<X>` other than `secrets.GITHUB_TOKEN`, or `secrets: inherit`. | Remove the dependency; this profile is OIDC-only. |
| `SEC-002` | Top-level permissions wider than `contents: read`, or `id-token: write` outside the `publish`/`pypi` job. | Narrow the permissions block. |
| `SEC-003` | `pull_request_target` or `workflow_run` declared anywhere. | Use `pull_request` instead. |
| `SEC-004` | A `uses:` action not in `[allow].actions`, or not pinned to a 40-hex commit SHA. | Allowlist it and/or pin it by SHA. |
| `RUN-001` | A `runs-on` label outside the fleet list, or any `-latest` label. | Use a fleet label: `ubuntu-24.04`, `ubuntu-24.04-arm`, `windows-2025`, `windows-11-arm`, `macos-15`, `macos-15-intel`. |
| `WF-001` | A job has no `timeout-minutes`. | Add one. |
| `WF-002` | A workflow has no top-level `concurrency`. | Add one. |
| `WF-003` | `continue-on-error` anywhere. | Remove it; fix or gate the step instead. |
| `TOOL-001` | Bare `cargo`/`rustc`/`rustup`/`cargo-*`/`maturin`/`cross`/`cibuildwheel`/`pip`/`pipx`/`twine`/`curl`/`wget` as a command (in `run:` lines or `ci/*.py`/root `*.py` subprocess literals). | Wrap it through `soldr` or `uv`. |
| `TOOL-002` | A dependency-resolving `cargo` subcommand (`build`/`test`/`check`/`clippy`/`doc`/`run`/`nextest`) without `--locked`. | Add `--locked`. |
| `CACHE-009` | `zackees/setup-soldr` used outside `[allow].setup-soldr.only-in`, missing/wrong `require` inputs, or an input enabling a retired cache family. | Call it only from the wrapper, with the required inputs. |
| `LAYOUT-001` | A host selector (`cfg(...)`, `sys.platform`, ...) outside `[allow].platform-code`/`platform-selector`. | Move it behind the platform facade. |
| `RUST-005` | More integration-test targets exist than are declared in `[rust.tests].binaries`. | Declare each target, or consolidate `tests/*.rs`. |
| `RUST-011` | A private crate without `publish = false` / with `[features]` / with an optional dependency / with `cfg(feature)`; the public crate's `[features]` not of the form `x = ["dep:<private-crate>"]`; or `--all-features`/`cargo hack`/`--feature-powerset`/an out-of-`[rust].ship` `--features` value. | Fix the crate's manifest, or use only a `[rust].ship` feature set. |
| `RUST-012` | An undeclared test target, a declared-but-absent one, or an enabled `<crate>:lib` harness whose crate has zero `#[test]`. | Reconcile `[rust.tests].binaries` with the actual Cargo targets. |
| `PKG-003` | `[project.scripts]`/`[project.gui-scripts]` shadows `[python].cli.name`, or `[tool.soldr.pep517].bundle-bins` omits `[python].cli.crate`. | Remove the Python shim entry; add the crate to `bundle-bins`. |
| `PKG-004` | `pyproject.toml`'s build backend isn't `"soldr"`, `requires` lacks an exact `soldr==` pin, maturin appears in build requires/dependency-groups, or `uv.lock` has a non-soldr package depending on maturin. | Use `soldr` as the sole backend and maturin dependent. |
| `PKG-005` | A `try/except ImportError` around an import of `._native`. | Import it unconditionally so a missing native module fails loudly. |
| `CACHE-001` | A raw `actions/cache` (or any of its sub-actions) used directly. | Use a declared cache family instead. |
| `CACHE-002` | A volatile component (`github.sha`, `github.run_id`, `github.run_number`) in a `key:`/`cache-key-suffix:` input or a `[cache.family].key` entry. | Key on `hashFiles(...)` or a date rotation instead. |
| `CACHE-004` | The proven worst-case cache footprint exceeds `[cache].budget`, or `[cache].budget` exceeds 10GB. `pre-prune = true` on every writer flow removes only the lockfile-change-peak term from the sum -- it does not waive the rest of the proof; `worst = steady + [cache.pr].budget` still must fit. | Lower family sizes/cardinality, raise the budget (up to 10GB), and/or set `pre-prune = true` on every writer flow (removes the lockfile-peak term only). |

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
| `ci-lint gate --repo <path> --plan <plan.json> --needs <needs.json>` | `ci-lint plan`'s own JSON output + GitHub's `toJSON(needs)` verbatim | The `CI OK` aggregator: every job in `plan.required_jobs` must have `needs[job].result == "success"` (a `skipped` job is `TEST-001`; a job id absent from `needs` entirely is `needs_review`, never a silent pass). If `plan.mergeable` is `false`, prints `not mergeable: tag(s) <tags> remove required coverage` to stderr regardless of job results. | 1 if not green |
| `ci-lint precheck --local` (or env `ACT=true`) | -- | Runs every static group as usual, then adds one `needs_review` finding per check that needs the GitHub API and is not implemented yet (`CACHE-005`/`006`/`008`, `ACT-001`), each explicitly labeled `skipped (local): ...` -- never silently passed. | as `precheck` |
| `ci-lint selftest` | -- | Runs this package's own `unittest` suite (`ci_lint.selftest`; zackees/zccache#1760 -- agents must be able to run this directly). | 0/1 |

Every command above is stdlib-only (`zipfile`, `tarfile`, `subprocess`,
`tomllib`, `json`) and every finding is a `ci_lint.finding.Finding`
(`--json` prints the same fields as `precheck --json`'s `findings` array).

A `[tags.<id>]` entry with `flow` set switches the base flow itself (e.g.
`[release]` on a PR runs the `release` flow as a rehearsal, with
`publish = "rehearsal"`).
