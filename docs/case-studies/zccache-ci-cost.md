# Case study: zccache CI cost — link-target fan-out fixed, workflow fan-out still open

Case studies record verified evidence from a specific repository incident or
investigation. They are not policy: a rule only binds once it is written into
[policy-general.md](../policy-general.md) or [policy-rust.md](../policy-rust.md).
This one covers `zackees/zccache`, gathered via `gh api`/`gh issue`/`gh pr`/`gh
run` evidence, not inference from workflow names. It has two independent
findings with opposite outcomes: one fan-out problem was fixed and measured;
a second, larger one is still open as of this writing.

## Finding 1 — integration-test link-target fan-out (fixed)

Cargo compiles **each top-level `tests/*.rs` file as its own executable**,
statically linking the full reachable dependency graph into every one. This
is the same pattern documented in
[clud-ci-cost.md](clud-ci-cost.md) for `zackees/clud` and `zackees/soldr`;
zccache is the corollary-bug repo in that case study (its `--test`
cache-admission bug, zccache#1525) but it turns out to have had the fan-out
problem itself, independently.

### Before (measured 2026-08-27, zccache#1526)

`crates/zccache/tests/` had **97 top-level `*.rs` files** (+1 in
`zccache-daemon-core`) = 98 link targets, against a **457-package**
`Cargo.lock` — soldr's own graph was comparable and hit a 3.3 GB archive and
disk exhaustion (see [clud-ci-cost.md](clud-ci-cost.md)). Only 5 of the 97
files shared the `common` helper; the rest each separately re-linked the
whole graph. `crates/zccache/Cargo.toml` additionally declared 11 `[[bin]]`
and 9 `harness = false` `[[bench]]` targets on top of the 97 test files, so a
full `cargo test`/`nextest` build already produced "well north of 110 linked
executables." No `cargo nextest archive` step existed anywhere in the 24
workflow files at the time, so artifact size was **unmeasured** going in —
the issue's own acceptance criteria required measuring it as a first step.

### After (measured, PR #1551, merged 2026-08-28T23:28:35Z)

| Metric | Before | After | Change |
|---|---|---|---|
| Integration-test link products (workspace) | 99 | **10** | −90% |
| On-disk bytes of those executables (macOS aarch64, dev profile) | **1,772.8 MB** | **399.2 MB** | **−77%** |
| Test functions collected | 3008 | **3008** | 0 (verified by diffing full test IDs, not counts) |

Eight category targets (`cli` 17, `cache_native` 17, `depgraph_fingerprint`
15, `cache_rust` 12, `daemon_lifecycle` 11, `perf` 10, `stress` 9, `wire` 6)
plus one deliberate holdout: `perf_bench_test` stayed its own link target
because an external pipeline (`perf-guard.yml`,
`ci/docker/standalone_perf_entrypoint.sh`) builds and runs it **by target
name**, glob-matches `deps/perf_bench_test-*`, and installs it as a
sha256-pinned artifact — folding it into `perf` would have put ten unrelated
modules inside a hashed, externally-contracted binary to save one link
product. Net: 98 files → 8 categories + 1 standalone = **9 targets** in
`crates/zccache`, against the issue's proposed "8 (or a justified nearby
number)" — a deliberate, documented deviation, not a miss.

The same PR caught and fixed real stale references the move tripped: two
`dylints/*/src/allowlist.txt` files that allowlisted banned-pattern
exceptions **by path** (paths that stopped existing), 11 doc-comment/README
path references, a `docs/architecture/kernal-api-migration.toml` evidence
table keyed by path, and a YAML parse break in `ci.yml` where a `run:`
scalar ending in `daemon_dylint_cache_test::` made the trailing `:` a mapping
indicator. A grouping-table set-diff against the live tree also caught one
file (`daemon_msvc_cl_cache_test`) the filed issue's plan had silently
omitted — exactly the "one file left behind" failure mode the issue itself
warned about.

### What is not measured here

**Wall-clock CI time before/after is not independently recoverable.**
zccache's `ci.yml` alone produced 300 runs between 2026-09-17 and
2026-09-28 (~30 runs/day) when sampled for this case study — at that
velocity, reaching back to the 2026-08-28 merge date would require many
thousands of paginated API calls, which was out of scope here. The verified
numbers above are structural (link-target count, on-disk bytes, test-ID
parity), not timing. Treat any "N minutes faster" claim about this specific
change as unverified unless a future investigation pulls the actual run
history.

## Finding 2 — workflow orchestration fan-out (open, and getting worse)

A different fan-out axis: **how many separate GitHub Actions workflow files
trigger on `pull_request`**, independent of what any single one costs. This
is tracked in zccache#1639 (open, filed against the state as of
2026-09-23) and was not touched by the #1526/#1551 fix above.

### The problem as filed (2026-09-23, zccache#1639)

- PR #1620 reported **33 checks from 10 workflows** (`CI`, `Linux`,
  `Windows`, `macOS`, `Integration`, `Filesystem Matrix`, `Perf Guard`,
  `Python Tests`, `Soldr Broker Stress`, `Wrapper end-to-end`).
- A repository inventory on the same date found **13 `.github/workflows/*.yml`
  files with a `pull_request` trigger** (path filters explain why only 10
  appeared on that specific PR).
- The issue explicitly does not claim this is redundant coverage — several
  lanes cover genuinely distinct filesystem, platform, wrapper,
  embedded-broker, Python, and performance contracts. The problem named is
  orchestration: `ci.yml` is already called `CI` but owns only a subset
  (fmt, Dylint, MSRV, docs); every other lane is its own independent
  `pull_request` entrypoint, so a contributor "can't tell whether the change
  is ready" without watching 10+ separate check groups.
- Proposed fix: make `ci.yml` the **sole** `pull_request` entrypoint,
  converting the other lanes to `workflow_call` jobs it invokes, with an
  explicit acceptance criterion to measure runner-minutes and wall-clock
  time before/after, and a proposed repository guard that fails if a future
  workflow other than `ci.yml` adds a `pull_request` trigger.

### Current state, verified today (2026-09-28)

The repository still has 27 workflow files (`.github/workflows/`), and a
just-merged PR (#1755, `gh pr checks 1755`) shows **59 checks** across at
least a dozen distinct workflow runs — `Broker stress`, `Cache pre-prune
barrier` (9 separate near-instant instances), `Dylint`, `Integration
(Linux)` (17m32s, the longest single lane), `MSRV`, three OS × {Check, Test}
matrices, `Reflink e2e`, multiple `wrapper-e2e` platform variants, `Windows
ARM64`/`x86_64`, `macOS ARM`/`x86_64`, and several `Observe macOS runner
queue` / speed-floor jobs marked `skipping`. That is **higher than the 33
checks #1639 cited five days earlier**, not lower — the fan-out this issue
is tracking has grown in the interval, and the issue remains open.

This is the honest answer to "how fast did we make it" for this axis: **not
yet**. The structural fix (#1639) is designed but not implemented.

## Candidate policy rule — adopted (round 6A, zackees/ci.yml#6)

[clud-ci-cost.md](clud-ci-cost.md) already proposes `RUST-005` (link-target
fan-out vs. dependency-graph size) and `RUST-006` (linked test products in a
persistent cache). This case study's Finding 1 is corroborating evidence for
both — zccache's before/after numbers are the most precisely measured of the
three repos surveyed. Finding 2 is a distinct axis and needed its own
candidate, chosen to avoid colliding with existing tables (`RUST-005`/
`RUST-006`/`GEN-005` proposed in clud-ci-cost.md, since renumbered `GEN-010`;
`RUST-007` proposed in soldr-ci-cost.md; `GEN-006`/`GEN-007` proposed in
fbuild-ci-cost.md):

| Candidate ID | Mechanical signal | Status |
| --- | --- | --- |
| `GEN-008` | More than one workflow file declares a `pull_request` trigger without a documented reason each must be independent (e.g. a hard `workflow_call` limitation), and no single entrypoint's job graph accounts for the full per-PR required-check surface. Contrast with `GEN-001` (missing entrypoint entirely) — this is the fragmented-entrypoint case, not the absent one. | **Enforced by ci-lint** (static precheck — see policy-general.md) |

`GEN-008` is now written into [policy-general.md](../policy-general.md)'s
rule table: `ci_lint` fails a repository with more than one workflow file
declaring `pull_request`, or any workflow file/trigger not declared in
`[allow].workflows` (`ci_lint/rules/workflows.py`). zccache's own #1639 (the
33-59-checks/10-27-workflow fan-out this finding is built from) remains open
upstream as of this writing — adopting the rule here does not imply zccache's
`main` currently passes it.

## Sources

- zccache: issues #1526, #1639; PRs #1551, #1552; run/check evidence from PR
  #1755 (`gh pr checks 1755`); workflow inventory
  (`gh api repos/zackees/zccache/contents/.github/workflows`); run-history
  sampling of `ci.yml` (2026-09-17 to 2026-09-28, 300 runs).
- Cross-referenced: [clud-ci-cost.md](clud-ci-cost.md) (clud#1056/PR#1060,
  soldr#2931 and phases, zccache#1525).

All issue/PR/run references were fetched live via `gh api` / `gh issue view`
/ `gh pr view` / `gh pr checks` / `gh run list` against the public
repository; none are inferred from memory.
