# Case study: fbuild CI cost — board-matrix fan-out and strict-without-queue amplification

Case studies record verified evidence from a specific repository incident or
investigation. They are not policy: a rule only binds once it is written into
[policy-general.md](../policy-general.md) or [policy-rust.md](../policy-rust.md).
This one covers `FastLED/fbuild` (not `FastLED/fbuild-ide`, a different
repository) — the fleet's primary CI-normalization fixture per
[proposal.md](../../proposal.md), with 76-80 board build targets across 13
board families. Evidence gathered via `gh api`/`gh run`/`gh pr`, not inferred
from workflow names.

fbuild's cost driver is a different mechanism from
[clud's](clud-ci-cost.md) — not Cargo static linking, but a large per-board
build matrix (~80 independent embedded targets: AVR, ESP32, STM32, CH32V,
Teensy, NRF, and more) combined with a branch-protection setting that
multiplies how often that matrix re-runs. The repository fixed both, in two
separate PRs a month apart, and independently converged on the same
label-gated tier shape clud already uses.

## Stage 0 — the incident: broad path triggers + strict serialization

Before 2026-08-24, `ci/ci_common_paths.txt` force-ran **every** per-board
workflow on any shared-code edit, by explicit design choice recorded in its
own comment: *"Bias: be BROAD here. A safety-net force-run on every
common-code edit is the right trade vs. missing a regression that only a
board build catches."* The list included `crates/fbuild-core/**`,
`crates/fbuild-cli/**`, `crates/fbuild-paths/**`, and the workspace manifest
and lockfile — paths a near-arbitrary edit touches.

- A one-line edit to `fbuild-core` scheduled all **80** board builds, ~94
  checks per PR (fbuild#1396, filed against `main`).
- `main`'s branch protection sets `required_status_checks.strict: true`
  (verified: `gh api repos/FastLED/fbuild/branches/main/protection` — still
  true today) with no merge queue (`mergeQueue: null` via GraphQL, `gh api
  repos/FastLED/fbuild/rulesets` → `[]`, zero `merge_group` events in the
  last 100 runs). `strict: true` requires a PR branch to be up to date with
  `main` before merging; without a queue to batch that work, **every merge
  to `main` invalidates every other open PR's status**, forcing each to
  re-run its full check set.
- Combined effect, observed directly by whoever filed #1396 "across a 14-PR
  session": wall-clock dominated by re-running ~20-minute full board sweeps
  for changes that could not affect the boards being rebuilt, with the PR
  queue serializing behind them.

## Stage 1 — scope the trigger to a core board set (fbuild#1396 → PR #1397, merged 2026-08-24)

Fix: give `ci/board_families.json` entries a `"core": true` flag. Shared-code
paths now trigger only the core set; family-specific paths still trigger
their whole family (unchanged). 17 core boards were chosen to cover all 13
families, with two representatives for families spanning genuinely different
toolchains (ESP32 Xtensa vs RISC-V, STM32 F1/M3 vs H7/M7, SAM M3 vs M4,
Teensy M4 vs M7). `nightly-platforms.yml` became the full-80 safety net,
scheduled daily.

Measured effect, recorded in the issue and independently visible in the PR
diff (82 files changed, each `build-*.yml` losing the same 50-line broad
`paths:` block):

| Change | Boards triggered before | Boards triggered after |
|---|---:|---:|
| `fbuild-core` one-liner | 80 | **17** |
| `fbuild-cli` change | 80 | **17** |
| Workspace lockfile bump | 80 | **17** |
| ESP32 orchestrator change (family-scoped) | 9 | 9 (unchanged) |
| Teensy linker change (family-scoped) | 8 | 8 (unchanged) |
| Docs-only change | 0 | 0 (unchanged) |

The same PR also added a missing `concurrency` guard to `hw-ci.yml`, the only
PR-triggered workflow that lacked one — without it, pushing to a PR branch
queued a second physical-hardware run behind the first instead of
superseding it, a correctness issue as well as a cost one since those jobs
occupy real boards.

**Independent verification (this investigation, current data):** the daily
full-80 safety net is real and its cost is measurable. Run `36414265987`
(2026-09-28, `nightly-platforms.yml`): **77 jobs**, wall-clock 11 min 27s
(11:12:30 → 11:23:57 UTC), aggregate job-time **33,338 seconds ≈ 9.26 hours**
of runner compute for one sweep — cheap in wall-clock because jobs run in
parallel, expensive in aggregate compute, and this is the intentionally
*infrequent* (once-daily) case. Per-job duration ranged from 665s (Silicon
Labs MGM240) down to 68s (ATtiny1604) plus a 5s guard job.

## Stage 2 — stop auto-triggering boards at all; converge on label-gated tiers (PR #1457, merged 2026-09-24)

A month after Stage 1, fbuild#1457 ("fractional board validation with
exact-SHA full gate") went further: board CI moved to the same shape clud
already uses.

```
ordinary PR / push to main   -> ci-minimal  -> Linux-only gate
PR labeled ci-test            -> ci-test     -> Linux + Uno
PR labeled ci-full            -> ci-full     -> 76 distinct board executions
                                                + Linux/Windows, Dylint, QEMU,
                                                  acceptance, benchmark,
                                                  fmt/docs/MSRV/board/crate
                                                  policy checks
```

Verified live in `.github/workflows/ci-minimal.yml`: the `linux` job runs
unless the PR carries `ci-test` or `ci-full`; `full` (`ci-full.yml`, a single
matrix job fanning out to the ~76-board `include:` list) runs only when
`ci-full` is present; a `selected-coverage` job asserts the result matching
whichever tier was actually selected. Per-board `build-*.yml` files were
correspondingly stripped of their own `on: push/pull_request: paths:` blocks
(verified: `build-teensy41.yml` now declares only `workflow_dispatch` and
`workflow_call`) — **the Stage-1 core-set auto-trigger on shared-code paths
is gone entirely**, not just narrowed. Routine PRs get zero board builds by
default; a PR needs an explicit label to get any embedded-target coverage at
all, same trade clud makes.

The PR body flags this migration as still provisional at merge time:
*"Publication intentionally fails closed until a trusted physical-board
runtime result exists... Live `ci-test`/`ci-full` runs and matched PR/main
runner-minute measurements are still required before merge."* — i.e. the
author recorded the same rigor this project's [agent-guide.md](../agent-guide.md)
asks for (verify before claiming), rather than asserting the tier change was
cost-neutral without measuring it.

## Remaining structural gap: strict-without-queue is still live

Stage 2 fixed the board-matrix fan-out but did not touch the Stage-0
amplifier: `main` still has `required_status_checks.strict: true` with no
merge queue and no ruleset (`gh api repos/FastLED/fbuild/rulesets` → `[]`,
confirmed again on current data). The required-context list is narrower than
what the tiered workflow computes: only `"Dylint"` is a required status
check; `"CI selected coverage"` — the job `ci-minimal.yml` defines to assert
the correct tier passed — is not in `required_status_checks.contexts`. This
mirrors [clud's `GEN-010` finding](clud-ci-cost.md#candidate-policy-rules--adopted-round-6a-zackeesciyml6)
(renumbered from the `GEN-005` this case study originally referenced — see
that case study for why) in category (a documented/intended gate that
enforcement doesn't fully back) but is a distinct mechanism: clud has *no*
protection at all and compensates with a hand-applied label; fbuild has
*strict* protection *without a queue*, which is a specifically named GitHub
anti-pattern — every merge to `main` forces every other open PR to restart
its check set, and a queue is the platform's own answer to that, still not
adopted here.

## Candidate policy rules — `GEN-006` adopted, `GEN-007` still pending (round 6A, zackees/ci.yml#6)

These were drafted from this case study's evidence. Finding IDs were chosen
to avoid colliding with the existing tables or with clud-ci-cost.md's
`RUST-005`/`RUST-006`/`GEN-005` (since renumbered `GEN-010`, see that case
study).

| Candidate ID | Mechanical signal | Status |
| --- | --- | --- |
| `GEN-006` | `required_status_checks.strict` is `true` (or an equivalent ruleset "require branches up to date" rule) on a branch with no merge queue configured. Every merge forces every other open PR's required checks to re-run, multiplying CI cost by concurrent-PR count instead of batching it once per merge window. | **Enforced by ci-lint** (live audit, `ci-lint audit` — see policy-general.md) |
| `GEN-007` | A CI trigger's `paths:` (or an equivalent common-paths list) is scoped broader than the actual blast radius of the changed code, so a large per-artifact/per-platform matrix (boards, targets, platforms) fans out on a shared-code edit that only a small representative subset can meaningfully validate. Prefer a core/representative subset for shared-code paths, full coverage for artifact-scoped paths, and a scheduled full sweep as the safety net — the shape fbuild converged on in two steps (Stage 1, then superseded by Stage 2's tier-label approach). | Candidate — not yet implemented |

`GEN-006` generalizes the Stage-0 serialization mechanism and the gap that
remains today; it is now written into [policy-general.md](../policy-general.md)'s
rule table and checked live by `ci_lint audit` (`ci_lint/settings_audit.py`).
`GEN-007` generalizes Stage 1's fix as an intermediate
pattern — worth keeping as a documented option even though fbuild itself
moved past it to full label-gating in Stage 2, because a repository whose
board/target coverage can't tolerate zero default coverage (unlike fbuild,
where `ci-minimal` accepts Linux-only) may prefer the Stage-1 shape instead
of Stage 2's all-or-nothing label gate. It remains a candidate: no `ci_lint`
rule yet compares a trigger's declared path scope against its actual
per-artifact blast radius.

## Sources

- fbuild: issues #1396, #1457 (via commit `9f4cc4dda4d6`); PRs #1397
  (merged 2026-08-24, commit `ea2a8fffd473dfce895c93ff203475e30fef7d04`),
  #1457 (merged 2026-09-24); `ci/ci_common_paths.txt`, `ci/board_families.json`,
  `.github/workflows/ci-minimal.yml`, `ci-full.yml`, `build-teensy41.yml`;
  branch protection and ruleset API responses for `main`; run `36414265987`
  (`nightly-platforms.yml`, 2026-09-28); commit history of
  `.github/workflows/build-teensy41.yml`.

All issue/PR/run/API references were fetched live via `gh api` / `gh issue
view` / `gh pr view` / `gh run view` against the public repository; none are
inferred from memory.
