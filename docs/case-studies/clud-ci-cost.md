# Case study: clud CI cost — test-target fan-out and the merge-queue gap

Case studies record verified evidence from a specific repository incident or
investigation. They are not policy: a rule only binds once it is written into
[policy-general.md](../policy-general.md) or [policy-rust.md](../policy-rust.md).
This one covers two related but distinct findings from `zackees/clud`,
`zackees/soldr`, and `zackees/zccache`, gathered via `gh api`/`gh run`/`gh pr`
evidence, not inference from workflow names.

## Part 1 — integration-test link-target fan-out

Cargo compiles **each top-level `tests/*.rs` file as its own executable**,
statically linking the full reachable dependency graph into every one. File
count alone does not predict cost; the per-target link weight (size of the
dependency graph) does.

### soldr: the incident

`crates/soldr-cli/tests/` had grown to 98 top-level files (+3 in
`soldr-daemon`, +1 in `soldr-cache`) = ~110 linked test binaries.

- Windows-gnu nextest archive: **3,302,138,143 bytes compressed** (run
  `33065541438`, commit `dcf145c4`). One shard exhausted disk (`os error
  112`) while a separate volume had 143.61 GiB free — the failure was the
  extraction target, not total capacity.
- A DWARF-strip mitigation (soldr#2883/#2886) was tried first and recovered
  only **~5%** (3.49 GB → 3.30 GB) — proof the bytes were duplicated static
  linking, not debug info.
- MSVC's equivalent archive for the same suite was **248 MB**, the existence
  proof that the suite itself wasn't inherently large.
- Two policies made it worse: soldr#1391's guards *required* the full linked
  archive to be warm-cacheable at zero misses, and zccache was admitting
  `--test` link products into its cache (see zccache#1525 below), so every
  run minted fresh multi-MB entries that could never hit again.

Fix, tracked as soldr#2931 (meta) with six phases:

| Phase | Issue | What |
|---|---|---|
| 1 Containment | soldr#2933 | Single extraction onto the workspace volume, disk guard, byte attribution |
| 2 Consolidation | soldr#2934 | 102 files → ~8 category targets (110 links → ~12 target). Grouping follows existing `.config/nextest.toml` override groups so filter migration is mechanical |
| 3 Store hygiene | soldr#2935 | Adopt zccache#1525's admission exclusion via pin bump |
| 4 Warning | soldr#2936 | `WARNING: LOTS AND LOTS OF TESTS DETECTED (>50)` at plan time and archive-producer time; `SOLDR_TEST_TARGET_WARN_COUNT` env override, default 50, default on, warn not fail |
| 5 Policy + guards | soldr#2937 | Machine-readable cache-ownership manifest (`cook` / `zccache-unit` / `none` / named exception); `Lint`-job guard that fails on a banned artifact class in a reusable store; retire the #1391 invariant |
| 6 Docs | soldr#2938 | Fix agent docs that mandated the opposite policy |

All six issues are closed.

### zccache: the corollary bug

`RUSTC_CACHEABLE_CRATE_TYPES = ["lib", "rlib", "staticlib", "proc-macro",
"bin"]` in `crates/zccache-compiler/src/parse_rustc.rs`. Cargo compiles a
test target with `--test` and **no** `--crate-type`; the parser's "default is
`bin`" fallback classified every test harness as cacheable. The unit test
named for the intended exclusion (`rustc_test_flag_makes_non_cacheable`)
asserted the *opposite* of its own name. A test binary's input closure is the
whole linked workspace, so any source edit invalidates it — every CI run
minted a store entry that could never hit again. Fixed in zccache#1525:
`--test` harness invocations are non-cacheable by default.

### clud: the same pattern, caught before it broke anything

A cross-repo survey (clud#1056, filed 2026-08-27 against `main`) found
`crates/clud-bin/tests/` at 23 top-level files against a **533-package
dependency graph — the largest in the survey** (zccache: 457,
`running-process`: 382). 23 is below the "definitely file" threshold of 50,
but was filed anyway on the dependency-graph criterion: 9 of the 23 files are
Windows-only and additionally link `windows` 0.61's ~14 feature areas,
`windows-sys`, and `winapi` as dev-dependencies — strictly more than the
shipping binary links.

PR #1060 (merged 2026-08-28) consolidated 23 files into 6 category targets
(`tests/<category>/main.rs`, old files as `mod`s):

| Metric | Before | After |
|---|---|---|
| Integration-test link products | 22 | **6** |
| On-disk bytes of those executables (macOS aarch64) | 44.8 MB | **26.2 MB** |
| Test functions collected | 2519 | **2519** |

Parity was proven by **diffing full test IDs** after stripping the new
`<category>::` prefix — 0 lost, 0 gained — not by comparing counts, which can
hide a module dropped under the wrong `cfg`. The stray `[[test]]` manifest
stanza that predated autodiscovery was removed as part of the same PR after
confirming nothing depended on it. As of this case study, the live tree still
shows exactly the 6 category targets from #1060 — the consolidation held.

## Part 2 — the actual clud CI cost driver (deeper investigation)

The link-target fix above did not explain the user's "tremendous slowdown"
complaint, because it wasn't the live cost driver. Investigating clud's
current CI behavior directly (not from the workflow's stated design) found a
different, larger problem.

### What `ci/ci_matrix.py::resolve_tier` actually does

```
pull_request, no ci-* label   -> "minimal"  -> 1 target  (linux-x64 only)
push to main                  -> "minimal"  -> 1 target
PR labeled ci-test             -> "extended" -> 2 targets (core: linux-x64, windows-x64-msvc)
PR labeled ci-windows          -> "windows"  -> 1 target  (windows-x64 only)
PR labeled ci-full / ci:full   -> "full"     -> 6 targets (all platforms)
merge_group                    -> "full"     -> 6 targets
workflow_dispatch (full, with verified SHA provenance) -> "full" -> 6 targets
```

`CLAUDE.md` documents the intended shape explicitly: *"Routine PRs and main
updates run Linux x64 build + unit tests only... the merge queue still runs
the full matrix."* Full tier is 6 platforms × {build, unit test, integration
test} + dylint + static + harness = **22 jobs**.

### Verified: routine PRs are cheap, but "full" runs constantly anyway

Sampling the last 30 merged PRs (2026-09-27T07:11 to 2026-09-28T11:52, ~28.7h
— roughly 25 PRs/day merge velocity) by their `ci-*` labels:

| Tier label | Count | Share |
|---|---|---|
| `ci-full` (22 jobs, ~16 min wall, ~128 runner-minutes) | 12 | 40% |
| none (minimal, linux-only) | 15 | 50% |
| `ci-test` (extended, 2 platforms) | 1 | 3% |
| `ci-windows` | 2 | 7% |

Two sampled real `pull_request`-event runs (36415317421 for PR #1538,
36405789489) both ran the full 22-job matrix; both PRs carried a
hand-applied `ci-full` label (`gh api .../issues/1538/timeline` shows
`actor: zackees`, applied 2026-09-28T11:22:40Z, 40s before the run started).

At the observed rate, that's roughly **10 full-matrix runs/day × ~128
runner-minutes ≈ 21 hours of aggregated runner compute per day** spent on a
tier CLAUDE.md frames as exceptional ("the merge queue still runs the full
matrix" — implying routine PRs shouldn't need it).

### Root cause: the merge queue that the docs assume does not exist

```
gh api repos/zackees/clud/branches/main/protection
  -> 404 "Branch not protected"
gh api repos/zackees/clud/rulesets
  -> []
gh api graphql: mergeQueue: null, branchProtectionRule: null
gh run list (last 100 runs): 0 events with event == "merge_group"
```

`resolve_tier`'s `merge_group -> "full"` branch is dead code: GitHub never
emits a `merge_group` event because nothing requires a queue before merging
to `main`. The documented safety invariant — "routine PRs stay cheap because
full validation always happens at the queue" — was never turned on at the
platform level.

The observed behavior is consistent with a human compensating for that gap:
the same account that merges these PRs (`zackees`) hand-labels ~40% of them
`ci-full` before merging, evidently as a manual substitute for the missing
enforced gate. This is worse on both axes at once — it costs far more than
the missing merge queue would have (every PR pays full-6-platform cost
instead of one queue run per batch), and the other 60% of PRs still merge to
`main` with **zero cross-platform validation**, which is exactly what the
docs claim doesn't happen.

This matches the failure mode `AGENTS.md` already names for the fleet:
*"Confirm coverage from commands and runs, not from a runner label."* A label
applied by convention is not a mechanism; only an enforced branch protection
rule or merge queue is.

## Candidate policy rules — adopted (round 6A, zackees/ci.yml#6)

These were drafted from this case study's evidence. `RUST-005` and `RUST-006`
are now written into [policy-rust.md](../policy-rust.md) (see its rule
catalog and "Features are private crates" section for their adoption status —
`RUST-005` is enforced today, in a narrower declared-budget form than
originally proposed here; `RUST-006` is declared but not yet fully enforced,
pending candidate `CACHE-007`). The third finding below is now
**`GEN-010`**, not `GEN-005`: `GEN-005` was already the ID `ci-lint` uses for
the shell/Python-delegation budget rule (proposal.md, [issue #3](https://github.com/zackees/ci.yml/issues/3),
implemented as `ci_lint`'s `GEN-005` — see policy-general.md), a collision
this case study's original numbering did not anticipate. The renumbering is
recorded in [issue #6](https://github.com/zackees/ci.yml/issues/6) ("Housekeeping found while writing this").

| Candidate ID | Mechanical signal | Status |
| --- | --- | --- |
| `RUST-005` | Per-crate top-level `tests/*.rs` integration-test link-target count is not evaluated against the crate's dependency-graph size; no consolidation into category `tests/<name>/main.rs` targets once the per-target link weight is large, even below a raw file-count threshold. | Enforced by ci-lint, narrower form — see policy-rust.md |
| `RUST-006` | A linked test binary, bench, example-for-tests, or doctest product is admitted into a cross-run cache or persistent store, classified by contents rather than by the store's own cache-vs-artifact label. | Declared (`[cache].never`), not yet fully enforced — see policy-rust.md and `CACHE-007` in policy-general.md |
| `GEN-010` (was proposed here as `GEN-005`) | A repository's documentation or CI code asserts that full/required validation is enforced at a merge queue or protected branch, but the repository has no corresponding branch protection rule or merge queue configured. Coverage claims must be verified against enforcement, not against workflow code that assumes enforcement exists. | Enforced by ci-lint (M2-23, round 2/#5) — live audit for the merge-queue/branch-protection half; see policy-general.md |

### `GEN-010`'s third claim shape: native-OS Dylint

M2-23 (ci.yml#5/#44) widened `GEN-010`'s mechanical signal beyond the
merge-queue/branch-protection pair above to also cover a stale native-OS
Dylint claim -- the shape issue #5's Gap 1 originally flagged: clud's
`docs/architecture/ci.md` once read *"Every PR runs native Linux, Windows,
and macOS Dylint"* while `_dylint.yml` ran host-only Dylint with no
`--target`, a `GEN-005`(-general, doc/code mismatch)-shaped finding.
Re-verified live for this round (`gh api repos/zackees/clud/contents/docs/
architecture/ci.md`, 2026-09-28): that stale claim is **gone**. The current
doc instead reads *"Every PR runs one Linux Dylint job for the custom late
lint: a host pass plus [Windows/macOS] cross-target passes"* (line 14),
and the table at line 273 spells out "Linux Dylint (host + Windows/macOS
cross-target)" per mode -- exactly the check-only cross-target pattern
issue #5's `RUST-008` candidate asks for, not a native-OS claim any more.
So clud is no longer a live `GEN-010`/native-Dylint fixture; the finding is
recorded here as **historical** (what issue #5 observed, and the fix
pattern it names), not a currently-reproducible one. `ci_lint.rules
.doc_claims`'s static scan (`check_gen_010_static`) is still fully
offline-checkable for this claim shape against any repository that DOES
still carry it -- see `ci_lint/tests/test_doc_claims.py`'s synthetic
RED/GREEN fixtures for the mechanics, since clud's own docs no longer
provide a live one.

`RUST-005` and `RUST-006` generalize Part 1 (soldr's incident, clud's
preemptive fix, zccache's admission bug). `GEN-010` generalizes Part 2 and is
not Rust-specific — it applies to any repository whose quick-gate design
depends on a slower gate elsewhere that turns out not to be configured. It is
a distinct rule from `GEN-006` (docs/case-studies/fbuild-ci-cost.md, now
implemented): `GEN-006` fires when protection exists but is misconfigured
(`strict` without a queue); `GEN-010` fires when a documented/intended gate
has *no* enforcement mechanism at all and a human or agent is compensating by
hand, which is exactly what fbuild's own `GEN-006` finding says clud's gap
"mirrors ... in category" (docs/case-studies/fbuild-ci-cost.md) while being a
distinct mechanism.

## Sources

- clud: issue #1056, PR #1060, `ci/ci_matrix.py`, `CLAUDE.md`, runs
  `36415317421`, `36405789489`, `36418266760`, `36410499044`; PR #1538
  timeline.
- soldr: issues #2931, #2933, #2934, #2935, #2936, #2937, #2938; run
  `33065541438`.
- zccache: issue #1525.

All issue/PR/run references were fetched live via `gh api` / `gh issue view`
/ `gh pr view` / `gh run view` against the public repositories; none are
inferred from memory.
