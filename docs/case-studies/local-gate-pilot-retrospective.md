# Retrospective: the local-gate pilot (ci.yml#166), 2026-10-01

The goal was for a PR to pass CI on its first push. CI must stay fast, the local run must catch failures before any push, and enforcement must be by machine, generic enough to roll out across the fleet. soldr was the pilot. Evidence for the incident itself is in [soldr-first-pass.md](soldr-first-pass.md). This document assesses the work: what worked, what didn't, and what to do differently next time.

## Outcome

| Metric | Before | After |
| --- | --- | --- |
| soldr PRs green on the first push | 3 of 7 (43%) | 3 of 3 since adoption, all attested (small sample, all pilot PRs) |
| Lint checks | remote `Lint` job, 153-163 s, sequential | local lint lane, 62 s, parallel; the remote job runs the same command |
| Linux build + tests | remote `Linux x64`, 504-555 s | local: rust lane 181 s cold (Clippy 16 s warm) plus bosn tests lane ~170 s warm |
| A push that skipped the local gate | full CI fan-out, then a red run | `CI mode` fails in seconds; the fan-out never starts |
| `main` cache-budget check | red on every push | green (soldr#3525) |
| Lint-class defects that reached CI | 5 of the day's misses | caught locally: 5 unformatted files, 2 stale tests |

## What was built

- **ci_lint, GATE-001..005, plus RUST-001 on every gate surface** (ci.yml #167, #169, #171): the mirror rule, a verify-first job graph, the tree-bound `Local-Gate:` trailer, a pre-push hook, the `first-pass` metric, isolation for self-hosted tools, and no bare cargo. `uvx --from git+…ci.yml@<sha> ci-lint` makes it usable from any repo.
- **Policy:**
  - where checks run (GATE-006, #172);
  - cache compression tiers measured on real payloads (CACHE-023, #173);
  - lane result cache (GATE-007, #177, scoped, not built).
- **soldr adoption:** #3524, then #3525 and #3526 shipped under the gate.
- **Fixed along the way:**
  - bosn #317 (v0.1.5) and #322 (v0.1.6);
  - soldr #3521 (NixOS linker), #3522 (crash-safe cook), #3523 (test daemon leak, container `.venv`).

## What worked

1. **Starting from evidence.** Every run and job ID was read before deciding anything. The first-push failures fell into classes, and the largest class (Lint and guard tests) was cheap to move local. That set the target correctly.
2. **Mirroring by construction rather than by convention.** The remote `Lint` job may run only the local gate's command, and `ci-lint local-gate lint`, itself a check in that lane, enforces it. On its first run it found four CI jobs that didn't depend on the verify job, and 28 bare `cargo`/`rustup` sites.
3. **An attestation bound to the commit's tree.** It needs no server, token or status API, and works offline, under act, and for forks. A rebase correctly invalidated it: the trailer is still there, but `stale`. Squash merges kept it valid because the tree was identical.
4. **Check mode, not `--fix`.** The old Lint job formatted in place and never failed. The gate failed on four test files, then on a script merged an hour earlier. Formatting drift is now visible.
5. **"No stricter than CI."** A gate that `main` can't pass attests nothing. Swapping `soldr lint deps` (a full `cargo deny check`) for CI's exact commands made the rust lane pass and stay faithful.
6. **Measuring before changing.**
   - Duplicate `CI` runs cost 3-21 s each, so they were left alone.
   - The cache-budget failure turned out to come from a `workflow_dispatch` run, not a PR run, and later from an allocation that was simply too small, not from eviction.
   - zstd showed restore cost is the same at every level, which turned "low compression for local speed" into a precise rule.
7. **Parallel sub-agents for independent fixes.** The bosn release and three soldr defects ran concurrently, each in its own sister worktree with explicit constraints (never run tests on the host, never bare cargo, never merge on red). All merged with regression tests while the main thread kept going.
8. **Fixing the tool and cascading the version in the same session.** bosn was fixed, released, installed on the host, and set as the gate's `min_version` floor. The floor then fired correctly on a shadowed 0.1.3 install.

## What didn't work, and the lesson

| What happened | Cost | Lesson, and where it now lives |
| --- | --- | --- |
| The first retrospective said "cache budget fails PRs". It was a `workflow_dispatch` "CI full" run. | A corrected claim, a wrong first fix direction | Read the run's `event` before attributing a failure. |
| `soldr ci-test` was run on the developer host to time it. A fixture leaked a daemon that held the real `~/.soldr`. | Every soldr build on the machine wedged, for every session | Tests of a self-hosted tool never run on the host: GATE-005, soldr#3516. |
| An orphaned container `ci-test` was SIGKILLed mid-`dylint cook`. | `lib.rs` truncated to 0 bytes; misdiagnosed as a linker/profile problem for three runs | Check `git status` first when a build "loses" code. In-place source mutation must be crash-safe (soldr#3518). Stop tools gracefully. |
| The isolated runner differed from CI in five ways: `cargo test` vs nextest, dev-loop profiles, job caps, `sh -lc` dropping PATH, doctest ordering after a relink | Five runs before parity | The isolated runner reproduces the remote job exactly (GATE-006 text). Diff its environment against the CI job before trusting a red result. |
| Local infrastructure had rotted with no CI of its own: unpinned base images, a bosn output-reaping bug, a stale socket, a version mismatch | Hours of yak-shaving before the first green isolated run | Every local runner needs its own smoke test in CI. bosn#324 covers the remaining version-check gap. |
| Host health decided the gate's result: two soldr installs fighting over one root, a NixOS linker regression, a root-owned `.venv`, another session's daemon | Several false reds unrelated to the change | The gate needs a host preflight ("doctor") that names the host problem instead of failing a lane. Not built yet. |
| A soldr worktree was created against soldr's Working Location Rule, and `safe-rm` couldn't clean session-created paths | Policy friction; a forced worktree removal | The owner overruled the rule: sister repos are allowed (clud#1696). Doc and tool rules should be reconciled before work starts. |
| A chained `git checkout -- .` wiped uncommitted bosn work | Recovered only because the patches were scripted | Never chain a tree-wide checkout. Script non-trivial patches so they can be replayed. |
| The 28 RUST-001 sites were annotated as exceptions rather than converted | Exception debt in soldr's workflows | Owner review is pending. Convert where `setup-soldr` can run first. |
| The compression timings were taken on a loaded host and had to be rerun (`bc` missing) | Approximate absolute times | Benchmark on an idle host with a harness that doesn't depend on optional tools. |
| The full gate takes 338-659 s when Rust changes, and a rerun skips nothing | Slower than the remote critical path for small edits | The lane result cache (GATE-007, #177). Per-check exclusions next. |

## Phase 2: GATE-007, the lane cache (#177, soldr#3529)

A rerun of the gate used to skip nothing unless the commit was unchanged. GATE-007 caches lanes locally by content. Four design revisions came out of piloting it in soldr:

1. **A broad exclusion that happens to match a mandatory input is harmless.** The runtime always re-includes mandatory inputs, so the static check reports only an exclusion that literally names one.
2. **Split lanes by inputs, not by tool.** Replaying history showed tool-shaped lanes reusable 0-3% of the time and input-shaped lanes up to 48%.
3. **Pins follow the lane's tools.** Every `Cargo.lock` bump was invalidating soldr's Python-lint lane, which cannot be affected by it.
4. **Prove exclusions with `strace`.** The audit caught `pyproject.toml`, which soldr reads, excluded from the rust lane, after a grep-based review had already suggested three wrong exclusions.

Result: docs-only change 748 → 76 s, Python/CI change → 104 s, revert to a passed tree → 0.7 s.

Along the way:
- Two owner directives became ratcheted ci_lint rules, both enforced in ci.yml's selftest and in soldr's gate:
  - **PY-002:** records are typed dataclasses, never tuples or dicts.
  - **PY-003:** no capture-then-wait subprocess output. Iterating a pipe is fine, and `running-process` is strongly encouraged.
- PY-003's first remedy, `DEVNULL`, collided with soldr's own no-swallowed-stdio rule (soldr#3389). The remedy became "capture to a file and forward it on failure".

What didn't work:
- Reusing a branch after its squash-merge produced conflicting PRs twice. Always branch fresh from `origin/main`.
- `strace` on a lane that leaves children running hung until killed. The audit now runs per lane.
- bosn scopes volumes per workspace path, so a fresh worktree starts cold (bosn#327, which also covers seeding from ancestor commits).
- The remote counterpart, setup-soldr's cache key, is still hand-built per caller. The proposed fix, an `auto` key with nearest-ancestor restore, is #185 / setup-soldr#552.

## Phase 3: GATE-008, attested skip of the remote quick gate (#189, #190; soldr#3531, soldr#3532)

The goal was to make ordinary-PR CI fast without losing coverage. Eight strategies were tried as experiments; [docs/designs/pr-critical-path.md](../designs/pr-critical-path.md) describes them, and [the ledger](../designs/pr-critical-path-ledger.json) records the pros and cons with sources. Three adversarial rounds then shaped the design.

**Result on soldr:**

| PR | GATE-008 decision | CI wall time |
| --- | --- | ---: |
| soldr#3531 (wiring; surfaces changed, base not opted in) | `not-opted-in` | 858 s |
| soldr#3532 (first attested, in-policy PR) | `trusted`: Lint was a 3 s no-op, Linux x64 skipped | **46 s** |

Both `main` push runs afterwards ran Lint and Linux x64 for real, and both passed. The #3532 change itself was strategy H4, the per-test toolchain-catalogue fetch. On its post-merge `main` run (36970591097), nextest took **207 s instead of ~480 s**, `ci-test` 381 s instead of 632 s, and `Linux x64` **478 s instead of a 692 s median**. Total test-seconds fell from 1,933 to 827, all 3,572 tests passing.

What worked:
- **Experiments before design.** History replay showed eligibility of 66% under the first design, rising to 78% once surfaces were defined as the closure of what the skipped jobs depend on. Two of the "obvious" levers turned out to be dead ends on soldr: crate-graph test selection (≥96% of test time is still selected) and smart cache inheritance (PRs already hit `main`'s cache at 100%).
- **Real-runner probes on an experiment branch, through an existing `workflow_dispatch` workflow (no new workflow file).** Two runs found the remote-only per-test cost that months of `nextest.toml` tuning had attributed to "cold broker start": a network catalogue fetch, 5.2 → 0.17 s per test with it disabled.
- **The pre-gate history made the catch-all argument.** Every one of the 9 `main` breakages on 2026-09-26 came from a PR merged with no green run, and the `main` push run caught each one. That made the post-merge run mandatory in GATE-008, not optional.
- **GATE-007's partial-pass caching** made reruns cheap. After a host-linker failure, the next gate run reused 3 lanes, and after a bosn failure it reused 4.

What didn't work:
- **Host soldr 0.9.27 on NixOS** cannot link fresh build scripts with `reld` (soldr#3520, fixed upstream but not installed here). A fresh worktree's rust lane failed until it ran with `SOLDR_LINKER=default`, which is a key input of the lane, so this is legitimate.
- **bosn reused a container across worktrees** (zackees/bosn#314): once bound to a deleted worktree (loud failure), and once nearly to a live sibling (silent wrong-tree test). The second case breaks the tree attestation that GATE-008 trusts, so it is filed as candidate GATE-009 (#196).
- **My own GATE-008 code passed a nested dict parameter**, which the owner caught. PY-002 now covers parameters and variables of complex types (#194), and the remaining debt is tracked as a ratchet (#193).
- **A `perf/**` branch prefix opts a PR into Perf Matrix** (~11 min, not part of the gate), and it hit an unrelated `soldr save` temp-file race (soldr#3533). Use `perf/**` only when you want the sweep.

## Still open

- **The one-week measurement:** `ci-lint local-gate first-pass` over at least 5 PRs by other authors, target >= 80% (#166).
- **GATE-008 follow-through:** read the audit-sample and `main`-run agreement after 20+ attested heads (#190); a ruleset requiring `CI mode` and `Lint` on soldr (owner action); candidate GATE-009 (#196).
- **PY-002 burn-down:** #193.
- **bosn → act execution:** zackees/bosn#302.
- **The bosn version check in the other commands:** zackees/bosn#324.
- **A host preflight for the gate:** proposed above, not filed.
- **Native macOS, Windows and arm64 lanes:** remote-only by design. They remain the residual first-push risk; #3505's arm64 exec-bit bug is the type case.
