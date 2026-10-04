# Design: the ordinary-PR critical path (GATE-008, attested skip)

**Status:** adopted as `GATE-008` ([#190](https://github.com/zackees/ci.yml/issues/190)), piloted in zackees/soldr. Umbrella issue: [#189](https://github.com/zackees/ci.yml/issues/189).

**Evidence:** [`pr-critical-path-ledger.json`](pr-critical-path-ledger.json) records each experiment, its pros and cons, and the adversarial rounds below. The policy text is in [policy-general.md](../policy-general.md#attested-skip-of-the-remote-quick-gate-gate-008).

## Problem

An ordinary soldr PR waited a median of **914 s** (p75 1,696 s) for CI. Its critical path is `Linux x64`: `soldr ci-test` takes ~630 s of it, and nextest ~480 s of that. Since the local gate (GATE-001..007) was adopted, every PR head arrives attested: the same command already passed locally on the same tree, and 5/5 PRs went green on their first push. The remote quick-gate jobs had become a ~14-minute re-confirmation.

## Strategies tried

| | Strategy | Experiment | Result | Verdict |
| --- | --- | --- | --- | --- |
| H1 | Trust the attestation | Replayed 80 merged PRs | 78% eligible; no remote-only step ever failed; the 9 pre-gate `main` breakages all came from PRs merged with **no** green run | **Adopt (GATE-008)** |
| H2 | Run only affected tests (crate graph) | Package closure × per-package test-seconds | Every Rust PR still selects ≥96%: `soldr-cli` holds 95% of test time and depends on everything | Defer (#191, multi-crate repos) |
| H3 | Smart cache inheritance | PR vs `main` step timings, hit rates | PRs already restore `main`'s cache at 100% hits; PR `Linux x64` (647 s) is no slower than `main` (692 s) | Hygiene (#185: cache-budget failures, not latency) |
| H4 | Faster tests | Local bosn probe, then real-runner probes (runs 36964344956, 36965017158) | Cheap tests: 0.2 s locally, 4–5 s alone on a runner, 10–15 s inside `ci-test`. Not CPU, tokens or `CI` vars: each fixture fetches the toolchain catalogue over the network. `SOLDR_MANIFEST_DISABLE=1` brings them to **0.17 s (30x)** | **Adopt (soldr#3530)** |
| H5 | Shard nextest | Calibrated scheduling simulation (483 s simulated vs 480 s measured) | 2 shards: 283 s, but about +160 s of setup per extra shard | Reject for now |
| H6 | Larger runners | 4-CPU probe | Not CPU-bound | Reject |
| H7 | Raise the cold-group concurrency cap | nextest.toml history | +19% contention tax | Reject |
| H8 | Content-skip non-Rust PRs | Part of E2 | Subsumed by H1 | Subsumed |

## How the design evolved

**Round 1 (as filed).** Skip `Lint` and `Linux x64` on attested heads, and make every `.github/workflows/**` change ineligible.
- *Attack, eligibility:* only 66% of PRs qualified. Most of the rest edited workflows that the skipped jobs never run, and that a remote `Lint`/`Linux x64` run would not validate either. **Change:** a surface is now only what the skipped jobs depend on, which gives 78% eligible.
- *Attack, `Linux x64` is not a pure mirror:* it also runs CI-only probe scripts. **Change:** a non-mirror skip job declares `covered-by` lanes (checked statically), and its CI-only scripts become surfaces.
- *Attack, self-weakening:* the head's own declaration decided the policy. **Change:** policy is read from the **base** commit, and the trailer must cover every lane the base declares.

**Round 2.**
- *Attack, is the release gate enough of a catch-all?* No. Before the gate, `main` broke 9 times in a day, and it was the `main` push run that caught each one. **Change:** the default-branch push run is mandatory. Verify never trusts a non-PR event, and the static check fails a skip workflow that has no push trigger.
- *Attack, GEN-021 reuse of a skipped PR run:* already refused, because a skipped job is not a `success`.
- *Attack, "Update branch" merge heads:* the merged tree was never gated as a whole. **Change:** trust requires the exact-tree `attested` state.
- *Attack, an audit keyed by PR number:* a PR would be permanently in or out of the sample. **Change:** key the sample by head SHA.

**Round 3.**
- *Attack, blocking vs shadow audit:* the sampled head simply runs the jobs, so a disagreement is a real failure. **Change:** a blocking sample; `mode = shadow` exists only for rollout.
- *Attack, forks and authors without write access:* **Change:** require the same repository and OWNER/MEMBER/COLLABORATOR.
- *Attack, missing history:* **Change:** fail closed (`base-unavailable`).
- *Attack, no branch protection:* with trust, the decisive checks finish in about a minute, so a ruleset requiring them becomes cheap. This is recommended as an owner action.

## Final design

The verify job runs `ci-lint local-gate verify --trust --github-output` with full history. It outputs `trusted`, `would_trust` and `trust_reason`. `trusted` is `true` only when **all** of these hold:

1. The event is `pull_request`.
2. The **base** declaration enables `[gate.trust]` (`mode = "enforce"`).
3. The PR has no full label (`ci-full`).
4. It is not a fork, and the author is OWNER, MEMBER or COLLABORATOR.
5. The head carries a `Local-Gate:` trailer for its **exact** tree.
6. The trailer's lane provenance covers every lane the base declares.
7. No path in the merge-base diff matches a surface. Surfaces are the declaration, files named in the gate's or a lane's argv, their statically resolvable tracked Python imports (transitively, from the base), the skip and verify workflows, the local reusable workflows and actions they reference (transitively), and the declared `surfaces`. Dynamic imports require explicit surfaces.
8. The head is not in the 1-in-N audit sample (hash of the head SHA).

Each declared skip job adds `needs.<verify>.outputs.trusted != 'true'` to its job-level `if:`. A job that should still report the protected status name, such as soldr's `lint-docs` → `Lint`, runs as a no-op when trusted.

**Catch-alls, in order:**
1. The default-branch push run (always real).
2. The audit sample.
3. The release candidate's exact-SHA full run (soldr#3343/#3344).

## Rollout to a fleet repository

1. Adopt GATE-001..003 (and GATE-007 lanes, if any), and record `first-pass` (GATE-004) at or above target.
2. Add `[gate.trust]` with `mode = "shadow"` for a week. Read `would_trust` / `trust_reason` from the verify job summaries.
3. Switch to `enforce`. Keep `audit-rate` at its default of 10 until the audit sample has run at least 20 heads without a disagreement.
4. Recommended: a ruleset requiring the verify job and the protected quick-gate status names.

## Measured on soldr

| | Before | After |
| --- | ---: | ---: |
| Ordinary-PR CI wall time | median 914 s (79 runs, 2026-09-25..10-02); soldr#3531, not trusted: 858 s | **46 s** (soldr#3532, `trusted`) |
| Linux x64 on `main` (still runs on every push) | median 692 s | **478 s** (run 36970591097, after H4) |
| nextest stage | ~480 s | **207 s** |
| Test-seconds (all / `cargo_front_door`) | 1,933 / 1,224 | **827 / 387** |

Remote PR wait for an eligible PR fell about 20x. The remaining remote work (`main`, `ci-full`, release) is about 30% faster, because H4 removed a network fetch from every fixture.
