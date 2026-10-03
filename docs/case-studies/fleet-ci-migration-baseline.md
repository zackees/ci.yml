# Fleet CI migration baseline — 2026-10-03 UTC

Publication update: the baseline and first-pass rerun correction are in
[ci.yml PR #237](https://github.com/zackees/ci.yml/pull/237), validated by all
813 selftests. Bosn's shared runner fix is reviewed clean and passed six engine,
four pinned-image and four bootstrap tests in isolation. It preserves the
runner's Node/npm PATH and collects bootstrap tests in normal pytest discovery.
Its full gate passed Python static and policy lanes; Rust initially encountered
a Soldr 0.9.28/0.9.27 shared-state ownership conflict. A retry uses a separate
persistent Soldr root without replacing the shared daemon and is pending.

A second shared speed issue is confirmed: the planner emits `cache_save=false`
for the read-only PR flow, which kernal-api passes explicitly to setup-soldr.
setup-soldr allows local act saves in `auto` mode but respects explicit `false`.
Resolve the planner/cache contract centrally with regression evidence, preserving
explicit cache-disable inputs and remote cache policy, rather than adding local
runner workarounds to individual repositories.

This is an evidence snapshot and rollout worklist, not binding policy. Queried the latest 50 workflow runs per repository using GitHub REST. These are workflow-run rates, not first-push PR rates or required-check rates. Windows differ by repository activity. Cancelled, skipped, and pending runs are excluded from the decisive denominator (success + failure); their counts are reported separately. Reruns are not deduplicated. No local speed claim is established by this snapshot.

| Repository | Sample window (UTC) | PR success / decisive | Push success / decisive | PR cancelled / skipped |
| --- | --- | --- | --- | --- |
| [zackees/zccache](https://github.com/zackees/zccache/actions) | 2026-10-02T22:10:51Z – 2026-10-03T00:38:26Z | 15/15 (100.0%) | 29/29 (100.0%) | 0 / 0 |
| [zackees/soldr](https://github.com/zackees/soldr/actions) | 2026-10-02T10:24:36Z – 2026-10-03T02:03:33Z | 15/15 (100.0%) | 13/15 (86.7%) | 0 / 0 |
| [FastLED/fbuild](https://github.com/FastLED/fbuild/actions) | 2026-10-03T00:43:14Z – 2026-10-03T03:12:53Z | 20/27 (74.1%) | 9/9 (100.0%) | 4 / 3 |
| [zackees/bosn](https://github.com/zackees/bosn/actions) | 2026-10-02T20:37:43Z – 2026-10-03T03:07:20Z | 15/18 (83.3%) | 16/16 (100.0%) | 13 / 0 |
| [zackees/clud](https://github.com/zackees/clud/actions) | 2026-10-03T02:26:53Z – 2026-10-03T03:39:58Z | 14/20 (70.0%) | 6/6 (100.0%) | 4 / 18 |
| [zackees/kernal-api](https://github.com/zackees/kernal-api/actions) | 2026-09-29T12:26:45Z – 2026-10-03T02:48:49Z | 10/12 (83.3%) | 6/9 (66.7%) | 18 / 0 |
| [zackees/running-process](https://github.com/zackees/running-process/actions) | 2026-10-02T12:36:34Z – 2026-10-03T03:24:23Z | 25/31 (80.6%) | 9/9 (100.0%) | 6 / 0 |

## Current evidence and next correction

1. **Kernal-api: investigate the test failure, then establish local/remote coverage parity and attestation verification.** The current default-branch `ci.yml` has no `local-gate verify` job; `ci.toml` declares `runner = "bosn-act"`, but `bosn.toml` exposes fmt/clippy/cargo-test tasks rather than the entire workflow. That is configuration intent, not proof of act2 execution or equivalent coverage. [Failed full PR run](https://github.com/zackees/kernal-api/actions/runs/37085333936): Linux failed “Run the full test suite”; full-coverage correctly rejected Linux failure and skipped platform jobs. [Failed push](https://github.com/zackees/kernal-api/actions/runs/36983751509): “Test the default facade”. Preserve native/platform/full-mode coverage; do not skip it based on Linux-only proof.

2. **Running-process: separate external service failures from real platform tests.** [PR run](https://github.com/zackees/running-process/actions/runs/37080463874) failed both Codecov upload and Windows x86 all-feature unit tests. Removing a remote service dependency alone cannot resolve the real test failure. Investigate test evidence before migration.

3. **Clud: validate the recent rollout before another broad change.** [PR run](https://github.com/zackees/clud/actions/runs/37092346055) failed the Linux unit suite in Python shard 2, then CI OK. Fetch and compare its local gate coverage and exact source tree; green main pushes do not prove first-push reliability.

4. **Bosn: preserve dependency-boundary checks locally.** [PR run](https://github.com/zackees/bosn/actions/runs/37088922239) failed “Verify kernal-api boundary and locked resolution”. A root `ci-attestations.yml` exists on the default branch, but its existence alone proves neither local coverage nor remote skip behavior.

5. **Soldr and zccache: retain warm artifact reuse and inspect remaining non-PR failures.** Soldr has a root `ci-attestations.yml`; zccache latest sampled PR/push runs are green. The older [CACHE-026 evidence](https://github.com/zackees/ci.yml/pull/236) (53% zccache main success, cache-barrier failures) is historical and must not outrank current failures without a wider fresh measurement. Zccache still has failed Cache Cleanup runs in this sample.

6. **Fbuild: inspect failures before ranking a specific migration.** Its owner is FastLED, not zackees. The sample includes failed platform-boundary research, ci-minimal, and subprocess-lint workflows; job evidence still needs investigation.

## Acceptance for each migration

- Preserve every required lint, test, platform and artifact check; map actual commands, features, execution host and target.
- Run the same workflow through bosn’s pinned act2 engine locally. Measure cold, warm, and no-source-change execution separately.
- Establish tree-bound per-job attestation verification before skipping remote PR jobs. A missing or invalid stamp fails closed; native tests require native or VM evidence.
- Restore the nearest compatible known-good ancestor artifacts; verify actual restore/save logs and local persistent volumes. Never claim a cache is effective from declared keys alone.
- Compare decisive first-push PR outcomes and required-job execution/critical path over a consistent rolling window; keep queue time separate.
- Attestation reduces redundant work. It does not make external services, audit samples, new dependency resolution or uncovered platform tests infallible. A 100% sample is evidence over that sample, not a guarantee.

The next step is the kernal-api coverage map and focused failing-test reproduction. No repository migration is marked complete by this baseline.

## Kernal-api rollout: first local implementation

Work branch `feat/bosn-act2-local-gate` in the sister checkout
`ci.yml-extern/kernal-api` contains the first migration step (not published or
completed):

- `ci/local_gate.py` submits the exact existing `ci.yml:linux` job in minimal
  PR mode to bosn. It accepts only a completed successful act2 run with the
  expected workspace, commit SHA, null dirty-snapshot field, workflow, job,
  mode, zero exit, and every selected job completed with none failed. It also
  checks the worktree stayed clean and at the same HEAD through execution.
- `[local.gate.lanes.linux-minimal]` records identical-input passes through
  ci-lint's existing lane cache. There are no input exclusions. Bosn owns the
  workflow execution and persistent machine cache; warm speed still needs
  measurement.
- A shadow `verify` job precedes Linux and Dylint; platform jobs retain their
  existing dependency on Linux. Every existing check still executes remotely.
  The local runner is not declared a conventional mirrored command because it
  executes the workflow itself: local-gate lint correctly leaves GATE-001 as
  needs_review. Remote attestation skips are not enabled.
- Focused RED → GREEN evidence covers unrelated source, dirty snapshots,
  upstream act, wrong workflow/mode/job, failed and incomplete verdicts. All
  153 existing/new CI guard tests pass; Ruff check and format checks pass for
  the new runner and its tests.

Runtime validation is active as bosn run
`ae6f3b50-42b9-4187-9dc1-5b8536a5fb56` for commit
`df5325b7310036a1a82531526606d15caa9525bb`, observed queued behind four active
local runners. The run record confirms pinned act2 `0.2.89-act2.1`. The earlier
unchanged-tree probe was explicitly cancelled while queued in favour of this
committed candidate, rather than restarted because observation timed out.

The full remote test failure is now identified: run 37085333936 passed 1,294
of 1,295 executed tests, with
`process::ape_launch::command_routes_a_real_image_through_its_loader` timing out
at 120 seconds. This remains a test reliability investigation; do not waive it
or claim the minimal Linux gate covers the full test graph.


## First-push PR measurement

The [first-pass ledger](fleet-first-pass-ledger.json) records merged PRs since
2026-10-02 00:00 UTC with matching PR workflow runs. Unlike the earlier
50-run sample, this measures distinct PR head SHAs, failures and reruns within
each PR's lifetime. Fbuild's entrypoint is `ci-minimal.yml`; the other six use
`ci.yml`. Merged PRs with no matching run and unmerged PRs are outside this
collector's denominator. Its branch-run query is capped at 100; the ledger
records these limits. Trailer presence is not proof of stamp validity.

| Repository | First-pass PRs / sampled | Trailer present on latest run |
| --- | --- | --- |
| FastLED/fbuild | 7/16 | 0/16 |
| zackees/bosn | 36/49 | 10/49 |
| zackees/clud | 19/34 | 0/34 |
| zackees/kernal-api | 3/8 | 0/8 |
| zackees/running-process | 0/3 | 0/3 |
| zackees/soldr | 15/15 | 15/15 |
| zackees/zccache | 5/9 | 0/9 |

A focused regression found the classifier counted a successful attempt-one
run followed by a rerun as first-pass, contrary to its documented contract.
`classify` now requires zero reruns. RED → GREEN passed, and all 813 ci-lint
selftests passed via the repository local gate. Duplicate cancelled runs of
attempt one on the same head retain their existing treatment.

Kernal-api's candidate is now running locally. Its shadow verifier completed
successfully in 1.131 seconds; the engine preparation took 85.1 seconds after
7.7 seconds of engine creation. Setup-soldr is currently installing the pinned
Rust toolchain in the fresh job, so fast warm execution is still unproven.
No remote skip is enabled and no migration is marked complete.

## Runner-parity failure and shared correction

Kernal-api run `ae6f3b50-42b9-4187-9dc1-5b8536a5fb56` is terminal failure.
Its verifier passed and 131 selected Python guard tests plus shell guards
passed. The next command failed because the pinned slim runner image has no
`pwsh` (exit 127); the failure-reporting step also has no `gh`. No product
compile or test verdict was reached. The gate correctly refused to stamp the
commit. This is runner parity, not evidence of a product test regression.

The next correction is shared in bosn, branch `feat/ci-runner-stock-tools`,
commit `9c2a5b2`: provision PowerShell 7.6.6 and GitHub CLI 2.102.0 from their
publisher release assets, pinned by SHA256, into a versioned generation of
act's tool-cache volume after seeding it. Probe both executables before
writing `.complete`; existing tool-cache saving retains that generation.
The runner image digest stays unchanged. Act receives the tools' bin path,
and workflows keep their stock commands. The Rust wiring is not yet fully
validated or published.

Verification so far:

- A real cold bootstrap in the exact pinned Ubuntu runner image verified
  both publisher archive digests and successfully executed both tools.
- A second bootstrap used a read-only install volume with Docker networking
  disabled; both probes passed without any download. Total container wall
  time was 11.392 seconds, including container startup and cleanup. This is a
  tool-bootstrap measurement, not full CI warm timing.
- Four focused tests pass, including real script execution against fixture
  archives, warm reuse without downloads, and checksum rejection without a
  completion marker. Ruff and file-length/include-base checks pass.
- The isolated `bosn run --task ci-engine-check` is running the Rust compile
  in persistent stack volumes. A first attempt failed because Docker could
  not create the nested target mountpoint below the read-only source bind;
  creating the ignored host `target/` mountpoint allowed the second attempt
  to start compilation. No host test suite was run.

The sister directory disappeared during this investigation. Kernal-api's two
commits were recovered from bosn's retained frozen source Git repository;
Git bundles of both working branches now live under ci.yml's
`.git/fleet-migration-bundles/` so another directory loss cannot discard them.

The bosn candidate's isolated compile completed successfully: 82.02 seconds
of Cargo work cold. A second identical isolated task completed with 0.21
seconds of Cargo work and 4.661 seconds total wall time, with no recompilation.
The same persistent stack volumes and immutable image were reused. This
proves the focused local engine compile is incremental; it does not prove the
whole kernal-api workflow is warm. Focused native engine regression tests
are now running through `bosn run --task ci-engine-test` before review and
publication of the shared fix.
