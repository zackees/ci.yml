# Case study: clud `main` push CI burn -- re-validating trees a PR already validated

Case studies record verified evidence from a specific repository incident or
investigation. They are not policy: a rule only binds once it is written into
[policy-general.md](../policy-general.md) or [policy-rust.md](../policy-rust.md).
This one has been: its candidate rule is **`GEN-021`** (default-branch CI may
skip only through verified reuse), adopted into policy-general.md with the
enforcement status stated there, and specified end to end in
[docs/designs/default-branch-verified-reuse.md](../designs/default-branch-verified-reuse.md).
Tracking: [zackees/ci.yml#156](https://github.com/zackees/ci.yml/issues/156).

All numbers below come from `gh api` / `gh run` / `gh pr` against the public
`zackees/clud` repository, window **2026-09-29T00:09Z to 2026-09-30T16:27Z
(40 h)** unless stated otherwise. Raw per-run data (runner seconds, event,
branch, conclusion for 191 `ci.yml` runs; tree identity and head-CI status for
75 merged PRs) was collected with the GitHub REST API and is summarized here,
not checked in.

## 1. What protects clud's `main` today

- **No merge queue exists.** `gh api repos/zackees/clud/rulesets` returns
  `[]`, `gh api repos/zackees/clud/branches/main/protection` returns 404
  "Branch not protected", and `gh api "repos/zackees/clud/actions/runs?event=merge_group"`
  returns `total_count: 0` -- no `merge_group` run ever (all re-verified
  2026-09-30). Yet clud's `ci.yml` (`CI OK`, the `ci-windows` branch: "It is
  safe as a PR signal because the merge queue always runs the full matrix
  before merge") and `docs/architecture/ci.md` (lines 18, 104-105, 323, 328)
  describe one. This is the `GEN-010` failure class (documentation claiming
  enforcement the settings do not have), first recorded for clud in
  [clud-ci-cost.md](clud-ci-cost.md) Part 2 and still open; filed as
  [zackees/clud#1651](https://github.com/zackees/clud/issues/1651).
- **PR CI tests the PR head, not the merged result.** clud's `ci.yml`
  checks out `github.event.pull_request.head.sha` (provenance pinning), not
  `refs/pull/N/merge`. The `push` run on `main` is therefore the only
  execution the merged tree ever gets.
- PRs are squash-merged in practice (the repository allows squash, merge
  commit and rebase; auto-merge off; delete-branch-on-merge off), frequently
  by tooling that can merge without waiting for green CI.

## 2. Runner burn

191 `ci.yml` runs, **2,533 runner-minutes** (sum of non-skipped job
wall-clock seconds; queue time excluded):

| Trigger | Runs | Runner-min | Share | Avg per run |
|---|---|---|---|---|
| `pull_request` (every PR push and label change) | 138 | 1,606 | 63% | 11.6 min |
| `push` to `main` | 50 (32 success, 10 cancelled, 8 failure) | 562 | 22% | 11.2 min |
| `workflow_dispatch` (full-matrix CI proofs for releases) | 3 | 364 | 14% | 121.4 min |

`main` pushes arrive at ~30 runs/day and cost ~337 runner-min/day (scaled
from the window). A current full `main` run, **36692419899** (commit
`0450379d`, after the unit lane was sharded), costs **951 s = 15.9
runner-min**: Static checks 28 s, Dylint 269 s, Build linux-x64 240 s, Clippy
linux-x64 122 s, unit shards 77 + 105 + 106 s, CI OK 4 s. Build and Dylint
(509 s, ~54%) are default-branch cache writers (`CACHE-003`/`[cache].write-on`);
the rest (442 s, ~46%) is pure validation.

## 3. How often the merged tree equals a validated PR head

Of **75 PRs merged into `main` since 2026-09-28** (tree SHAs from `GET
/repos/zackees/clud/git/commits/{sha}`, head CI from `gh run list --workflow
ci.yml --commit <head>`):

| | PRs | Share |
|---|---|---|
| merged tree byte-identical to the PR head's tree | 60 | 80% |
| a successful `pull_request` `ci.yml` run on the head | 59 | 79% |
| **both** (the reuse-eligible population) | **45** | **60%** |

The other 40% reached `main` without that proof, so the `main` run is their
only test and must keep running.

## 4. What the `main` runs actually caught

100 `main` push runs, 2026-09-26 to 2026-09-30: 73 success, 12 failure, 15
cancelled. The 15 cancelled runs are all `Static checks` cancelled on
09-28/29; their cause is **unknown** (not analysed). The 12 failures,
classified against each commit's PR (tree identity and head CI):

| Failures | Test | Cause | Would tree-identity reuse have skipped it? |
|---|---|---|---|
| 4 (09-27 20:04-20:21; PRs #1514, #1515, #1513, #1512) | `tests/test_release_gate.py::test_release_workflow_gates_every_publish_path` | One broken-`main` incident: #1514 and #1515 had no successful PR CI on their head (merged without green CI); #1513 and #1512 had green heads but a different merged tree (they inherited the broken state) | No (no validated identical head) |
| 7 (09-29 21:11 to 09-30 02:54; PRs #1575, #1579, #1580, #1583, #1588, #1589, #1590) | `tests/test_codex_installer_rm.py::test_pinned_codex_installer_in_clud_child_environment[*]` | Flaky test (leftover `clud-rp-*.sock` race, reproduced under load); every one had tree == PR head and a green PR run. clud#1582 was closed by clud#1596 (merged 2026-09-30T04:46Z); whether that fixed every variant is tracked in zackees/clud#1651 | **Yes, all 7** (pure noise removed) |
| 1 (09-30 10:44; PR #1630, `main` run 36704217465) | `tests/test_refresh_model_contexts.py::test_committed_seed_covers_the_reported_model` | Real merge interaction or data drift: merged tree != head tree although the head was green; the next `main` run succeeded | No (tree differs) |

**A tree-identity reuse check loses zero real catches in this sample and
removes the flake-driven red `main` runs.** Caveat: 5 days, 75 PRs, PR
association via `GET /repos/{r}/commits/{sha}/pulls`; strong but thin
evidence.

### 4.1 The decision procedure replayed on real clud data

`ci-lint reuse-check` (Phase 0 of the design) was run live against
zackees/clud on 2026-09-30 with `--record`, and the trimmed recordings are
the checked-in fixtures under `ci_lint/tests/fixtures/runtime/default-branch-reuse/`:

| Push | PR | Result | API calls |
|---|---|---|---|
| `1ab2f4e4` (`main` run 36743212427, green) | #1643, head `8f8bc6c4`, PR run 36742138072 | `verified`: tree `3410191f` equal, 7 minimal-tier jobs green and fresh | 5 |
| `37288c01` (`main` run 36631561921, red: the flake) | #1575, head `27c81ca0`, PR run 36630585480 | `verified` with the pre-shard job names; `required-job-missing` with today's sharded names (a rename fails closed) | 5 |
| `8c20d604` (`main` run 36704217465, red: the real catch) | #1630, head `17983132` | `tree-mismatch` (`e078050e` vs `230a730a`): must run | 3 |

Every recorded PR run's `pull_requests[]` array was **empty** after its PR
merged, so a check binding the proving run to the base SHA it tested against
cannot be built from run data (design section 3.3).

## 5. Estimated savings

Scaled from the 40 h window at clud volume (design section 10 has the
formula and sensitivity):

| Design | Saves per day | Share of all CI runner time |
|---|---|---|
| Skip everything on eligible `main` runs (60% of runs) | ~200 runner-min | ~13% (60% of `main` burn) |
| Skip Static/Clippy/unit/CI OK, keep Build + Dylint as cache writers | ~94 runner-min | ~6% |
| Skip everything, add one nightly cache-writing run | ~190 runner-min net | ~12% |

The design refines these (the decision host and `CI OK` always run; the
scheduled run is a full-tier drift backstop, 15.9 runner-min/day): strategy
(i) keep writers ~71/day net, strategy (ii) skip everything ~180/day net.
Second-order: less `main` load shortens PR runner queues (44-149 s observed
when several CIs overlapped; not quantified). clud's Linux build cache is
currently frozen by an oversize-payload skip (zackees/ci.yml#153), so
skipping its `main` write costs nothing today; the Dylint cache is a real
`main`-only writer.

## 6. Why not "just enable a merge queue"

A merge queue would make the exact merge commit the thing validated, and
GitHub then fast-forwards `main` to that tested commit, so a `main` re-run
would be truly redundant. But clud's docs promise a **full-matrix** run per
queued merge (a full `workflow_dispatch` proof costs ~121 runner-min versus
~16 for a minimal run) at ~30 merges/day, and its merge tooling relies on
immediate merges. Enabling a queue without restricting it to the minimal tier
would multiply burn. The policy therefore allows a merge queue as an
alternative justification only when live settings prove it (`GEN-010`'s live
half) and otherwise requires verified reuse.

## 7. Decision

Adopted as **`GEN-021`** in [policy-general.md](../policy-general.md): a
default-branch push run may skip a required job only through verified reuse
-- tree identity with the one merged PR's head, per-job proof from the newest
decisive `pull_request` run of the same workflow on that head, a freshness
bound (default 24 h), fail-closed on any uncertainty, visible provenance in
the aggregator, and cache writers exempt unless their writes move to a
schedule -- never through a blanket `if:` or a deleted `push:` trigger,
unless a live merge queue validates the exact merge commit. `pull_request` CI
that tests the PR head does not validate the merged tree unless the
tree-identity condition holds.

What `ci_lint` enforces today (Phase 0): the static half (`ci_lint.rules.default_branch_skip`,
a blanket default-branch skip of a gate-required job with no reuse decision
is a violation; unresolvable conditions, hand-written reuse flags and
uncovered skips are `needs_review`) and the runtime decision
(`ci-lint reuse-check`, plus `ci-lint reuse-report` for shadow evidence). A
live scan of the current workflows of template-python-rust-cmd, clud,
zccache, kernal-api and setup-soldr found no `GEN-021` finding; clud is
compliant today precisely because it re-runs everything on `main`. The
aggregator-provenance, cache-writer and "documented as validated by the PR"
clauses stay candidate until Phase 2/3 (#158, #159).

Rollout: clud shadow mode (#157), promotion to enforce after 14 days and 100
decisive runs with zero unexplained false reuse and the flake fix confirmed
(#158), fleet-scan detection `FLEET-003` (#159), template adoption (#160).

## 8. Open questions

- **15 unexplained cancelled `main` runs** (`Static checks`, 09-28/29): cause
  unknown; Phase 1 inspects them.
- **Sample size**: 5 days / 75 PRs / 100 `main` runs. The shadow window
  (>= 14 days) and `reuse-report` over longer history will restate
  eligibility and the zero-loss claim.
- **Environment-drift exposure**: `test_committed_seed_covers_the_reported_model`
  depends on remote data; a green PR tree can fail on `main` for reasons the
  tree does not capture. Bounded by the freshness window and the scheduled
  full run, not eliminated.
- **Association lag**: whether `commits/{sha}/pulls` lists the PR within
  seconds of a merge is unmeasured; a lag costs a full run, never a skip.

## Sources

- clud runs: 36692419899 (current full `main` run), 36743212427, 36704217465,
  36631561921 (`main` pushes of #1643, #1630, #1575), 36742138072,
  36630585480 (PR runs of #1643, #1575).
- clud PRs: #1512, #1513, #1514, #1515, #1575, #1579, #1580, #1583, #1588,
  #1589, #1590, #1596, #1630, #1643; issues #1582, #1595.
- clud settings: `rulesets`, `branches/main/protection`,
  `actions/runs?event=merge_group`, repository merge settings (2026-09-30).
- zackees/ci.yml: #153 (frozen clud build cache), #156 (tracking),
  #157-#160 (rollout), zackees/clud#1651.
