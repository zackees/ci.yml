# Case study: kernal-api phase 2 -- first ci.toml and ci-lint baseline (#37)

Case studies record verified evidence from a specific repository incident or
investigation. They are not policy: a rule only binds once it is written into
[policy-general.md](../policy-general.md) or [policy-rust.md](../policy-rust.md).

This is the first slice of [#37](https://github.com/zackees/ci.yml/issues/37)
(round M2-32, meta #52): apply the schema-3 `ci.toml` contract that
[template-python-rust-cmd](template-python-rust-cmd.md) proved to
[zackees/kernal-api](https://github.com/zackees/kernal-api), then measure how far
kernal-api is from it. Nothing in kernal-api's workflows was fixed in this slice;
the goal is a declared contract, a strict validation step in its CI, and a
numbered list of findings. "unverified" marks a number no command below produced.

## Inputs

- kernal-api `main` @ `f1a17d8` (2026-09-28), plus the ci.toml added in
  [kernal-api#374](https://github.com/zackees/kernal-api/pull/374).
- ci-lint at `0296e9b4381b94c2e8621519c6ba1bedda58db4a` (the `linter` pin).
- Live cache listing: `gh api repos/zackees/kernal-api/actions/caches` and
  `.../actions/cache/usage` on 2026-09-28: **9,212,387,876 B in 40 active
  entries** (46 listed, incl. PR-ref entries).

## What the ci.toml declares

kernal-api is not the template's shape, so the file records its real shape
rather than copying the canonical example:

| Area | Declared | Source |
| --- | --- | --- |
| `profile` | `rust-library` (not `rust-pypi-app`) | one Rust facade crate + a pure-Python hatchling package; no native wheel/CLI |
| Platforms | 6 hosts: linux x64/arm64, windows x64/arm64, macOS arm64/x64 | `linux` job + `test` matrix (ci.yml:605-625) |
| `[rust]` | `public = "."`, `private = "crates/private/*"` (none exist), `ship = [["default"]]` | AGENTS.md: private crates only when measured timings justify them |
| Test binaries | `lib`, 9 `tests/<category>/main.rs`, 3 justified top-level tests, 4 `[[bin]]` harnesses | AGENTS.md test-layout rule; Cargo.toml `[[bin]]` |
| Suites / flows | `unit` (required), `python`, `native`; `pr` = linux-x64, `ci-test`/`ci-full` tags add `native`/all platforms | ci/ci_mode.py modes minimal / ci-test / full |
| Cache families | registry, deps (cook), compile (build-cache), sdk (cross-targets), toolchain (solo-toolchain), dylint, dylint-out, soldr-mini, uv | every prefix seen in the live listing |
| `[cache].budget` | 9.5GB | `BUDGET_BYTES` in kernal-api `ci/prune_obsolete_cook_caches.py:30` |
| `[publish]` | `auth = "token"`, environment `release` | release.yml:243 uses `secrets.PYPI_API_TOKEN` -- declared, not hidden |

## CI step added (kernal-api#374)

The `linux` job checks out zackees/ci.yml at the `linter` SHA and runs
`uv run --no-project python -m ci_lint plan --repo . --event-name "${GITHUB_EVENT_NAME}"`.
`plan` loads ci.toml strictly (any `CT-*` schema error exits 1) and prints the
run's selection. Locally: rc=0 for `pull_request` (with `[ci-full]`: 6
platforms, suites native/python/unit) and for `push` (flow `main`,
`cache_mode = write`). The full `precheck` is **not** gating, because it
reports 974 violations today (below); making it gating is #95.

## `ci_lint precheck --local` (static)

`uv run --no-project --with pyyaml python3 -m ci_lint precheck --repo <kernal-api> --local`:
**974 violation(s), 0 exceptions, 9 needs_review** (12.3 s). By rule:

| Rule | Count | Example | Follow-up |
| --- | --- | --- | --- |
| LAYOUT-001 | 696 | `ci/native_proof.py:202` `platform.system()`; `tests/wasm_worker_containment.rs` 89, `src/crash/mod.rs` 68, `src/snapshot/*` ~160 | #94 (reconcile with kernal-api's own platform Dylint baseline first) |
| RUST-011 | 144 | `[features].wasm-component-hash-experiment = ['dep:wit-bindgen']` is not `dep:<private crate>` | #94 |
| GEN-005 | 63 | tracked `.sh`/`.ps1` scripts, multi-line `run:` steps | #94 |
| CACHE-013 | 13 | setup-soldr/setup-uv steps that can save on a PR ref without a `pr-<N>` key component | #92 |
| RUN-001 | 12 | `jobs.prepare.runs-on = 'ubuntu-latest'` | #91 |
| WF-001 | 9 | `full-ci-gate` has no `timeout-minutes` | #91 |
| SEC-002 | 8 | `permissions.contents = 'write'` at top level | #91 |
| CACHE-009 / RUST-001 | 7 / 7 | setup-soldr called outside a `.github/actions/soldr` wrapper | #92 |
| RUST-013 | 7 | setup-soldr `version: 0.9.23` exact pin | #91 |
| SEC-001 | 2 | `secrets: inherit` (auto-release.yml:82) | #91 |
| PKG-004 | 2 | hatchling backend, expected soldr | #90 (profile scoping) |
| GEN-007 | 2 (needs_review) | `pull_request` without `paths:` while fanning out a 5-entry matrix | -- |
| CACHE-004 | 1 | static worst case 28,521,267,200 B > budget 10,200,547,328 B | #93 |
| CT-006, GEN-004, REL-002, TAG-003, TOOL-002, WF-002 | 1 each | no `[workspace.metadata.soldr].targets`; no `fast` job; `.gitignore` misses `target.soldr-*`; no `edited` PR type; `cargo test` without `--locked`; release.yml has no concurrency | #91, #94 |

The needs_review entries (ACT-001, CACHE-005/006/008) are the `--local` skips of
the live checks; the live audit covers the cache ones below.

## `ci_lint units` and `ci_lint tests size`

Artifacts from `soldr cargo test --locked --lib --no-run --message-format=json`
(the PR-mode "Test the default facade" graph; 56 s wall, warm local cache):

- `units`: 1 crate, 2 distinct units (custom-build + lib test), **0 violations**
  once `ship = [["default"]]`. With `ship = [[]]` it reported RUST-011 for
  `['default']` although `default = []` -- filed as #89.
- `tests size`: `kernal-api:lib` = **50,196,808 B** (cap 250MB). The other 16
  declared binaries report RUST-012 "not produced", which is expected for a
  `--lib` build and is not a finding.

The full-mode graph (`--all-features --tests --lib --bins --no-run`) did **not**
build locally: `io-extras 0.19.0` failed with E0554 (`#![feature]` on stable,
from its `can_vector` probe). CI's full mode builds it, so this is a local
environment difference; sizes for the 16 other binaries remain **unverified**
here (AGENTS.md cites ~180 MB each and 619 MB total, not re-measured).

## `ci_lint cache audit` (live, read-only)

Run through a `gh api`-backed fetch so no token was handled directly:
**46 entries, 9,212,387,876 B = 90.3% of the 9.5GB budget.**

| Family | Entries | Bytes |
| --- | --- | --- |
| compile | 3 | 2,780,568,859 |
| sdk | 4 | 2,428,098,095 |
| toolchain | 7 | 1,342,623,431 |
| registry | 8 | 1,337,644,426 |
| dylint-out | 1 | 619,590,808 |
| dylint | 1 | 544,405,988 |
| deps | 1 | 127,235,799 |
| soldr-mini | 3 | 30,725,837 |
| undeclared (setup-uv) | 18 | 1,494,633 |

Findings: CACHE-001 x18 (every `setup-uv-1-` entry -- setup-uv v6's prefix,
which ci-lint does not know; #87), CACHE-005 x1 (`setup-soldr-cargoregistry-v1-macos-x64-...`
is 237 B, poisoned), CACHE-006 x5 (registry entries whose trailing segment is a
per-job suffix hash -- likely a classifier false positive, #88), CACHE-008 x6
(entries on merged PR #360's merge ref), CACHE-004 (90% of budget), and the
GEN-009 rollup. kernal-api's own `cache-retention` job already prunes to its
budget; the gap is that its model is not the declared one (#93).

## Status against #37's acceptance

| Acceptance item | Status |
| --- | --- |
| ci.toml (schema 3) describing platforms/suites/cache families | done (kernal-api#374) |
| `precheck` clean at a pinned linter | **not met**: 974 violations; #91, #92, #93, #94, then #95 turns the gate on |
| `cache audit` within budget | **not met**: 90.3% live, static worst case 2.8x budget (#93) |
| D1-D5 scenario evidence | not started (#93) |
| ci-lint defects found on the way | #87, #88, #89, #90 |
