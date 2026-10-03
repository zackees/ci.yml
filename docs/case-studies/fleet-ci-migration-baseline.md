# Fleet CI migration baseline — 2026-10-03 UTC

## Latest validation: kernel shadow rollout merged; shared permission fix under review

Kernal-api's full remote run `37101690833` passed Linux, Dylint and all five
native platform build/test lanes. [PR #399](https://github.com/zackees/kernal-api/pull/399)
merged as `d8046bd`. Its two-lane shadow-attestation follow-up
[PR #400](https://github.com/zackees/kernal-api/pull/400) passed the minimal
remote run `37104952792` and merged as `4c0b06f`. Rebase preserved the previously
validated tree `f1f6329e2d5f`; the declared local gate reused that tree-bound
attestation without allocating an engine. Remote skipping remains disabled.

The cache-policy draft resolved `setup-soldr@v0` to the promoted
`d17a58eb2cea17a89af0824fb7c6b24ee68f5083` and saved local caches even though
the planner prohibits PR-context remote uploads. A subsequent changed-tree
run restored the stable toolchain and compiled build cache. Failed Linux
gates took 317 then 212 seconds; these are failure timings, not complete
passing two-lane warm timings. The boundary scanner's separate nightly
toolchain still downloads repeatedly. The draft now has 161 passing fast
guards. A pinned YAML parser rejects malformed workflow syntax before engine
allocation; the previous trailing-colon scalar was rejected and its folded
replacement parsed successfully.

The newly included default APE test still found no filesystem loader directory.
Moving the cache beside the executable and copying executable permissions
did not fix it. A proposed private-directory helper then failed to compile
because it requires the optional `fs` feature; that gate failed in 145 seconds.
Those fixture edits are removed, preserving the original default-feature
test and its assertions.

A live act2 Docker-execution regression in an owned harness reproduced
`umask 0000`. The shared candidate sets `0022` only for emulated hosted runner
images, using an argument-preserving `exec` wrapper. Its live regression
passed mask, literal metacharacters, working-directory and exit-status checks.
An actual workflow fixture passed in 9.801 seconds: hosted runner directories
and files were `755` and `644`; an explicit job container retained `0000`.
The non-image container regressions and focused runner regressions passed.
The shared fix is not yet merged, released or pinned by bosn, and the kernel
APE test has not yet passed on that runner. This is evidence for the next
shared correction, not proof of a completed cache-policy migration.

## Earlier checkpoint: shared action promoted; kernel runtime still incomplete

The exact-main fbuild canary `37101793881` passed all full platform and board
jobs, `CI selected coverage`, and `full / Full coverage`.
[Fbuild PR #1634](https://github.com/FastLED/fbuild/pull/1634) merged as
`6acb66a2f54ff879dc0e23b3577861b69c80cbe1`. Setup-soldr's exact-main contract
`37101549069` passed. Promotion dry run `37103509706` verified the contract,
coverage aggregators, and logs proving execution of the exact action SHA.
The prescribed promotion `37103685561` succeeded and `v0` now resolves to
`d17a58eb2cea17a89af0824fb7c6b24ee68f5083`. Local reuse timings after consumer
adoption still need measurement.

Kernal-api's corrected remote full Linux job passed in run `37101690833`;
its other native platform lanes are still running. The local full act2 run
`3fae8064-872f-4d7e-bda2-5097d3bc5777` completed in 1988.9 seconds:
all five original build-ID/unwind failures passed, but a nested APE-launch
test failed (1296 passed, one failed, 36 skipped; three leaky). That failure
was an assertion that the embedded loader had a filesystem directory. The
backend also supports an embedded memfd loader, which cannot serve a
grandchild; the exact filesystem cause is not proven yet.

The consumer draft places that test's private loader cache next to its
executable, on a filesystem proven executable, and retains every nested
launch/output assertion. It also runs the existing APE launch tests in the
minimal lane and removes the old minimal build-ID exclusion now that the
package explicitly emits that identity. A new attestation gate names the APE
checks. Fresh runtime validation is running; these edits are not yet a proven
fix or merged migration.

The cache-policy draft has 160 passing fast guards and a clean review. A
current precheck matched its parent's 916 violations and 13 review items
exactly before disabling the new verifier's setup-uv saves; that correction
removed `CACHE-003` and `CACHE-013`, leaving 914 violations. This rollout is
not evidence that the repository meets every current fleet policy rule.
The fast preflight caught a guard mismatch after changing the default test
commands in one second, before allocating the engine. The corrected guard
allows only the two exact default-mode commands, checks their mode conditions,
and forbids `--skip`; full-mode graph enforcement remains intact.

## Kernal-api: two local compile lanes proven; rollout and reuse still pending

[Kernal-api PR #399](https://github.com/zackees/kernal-api/pull/399) publishes
the first rollout. The combined tree with current main passed the declared
minimal Linux gate in 303 seconds, stamping `fe9f5aec145e` for tree
`9352ba2ffcaa`. Run `b462219d-eb7f-4cbb-9943-a2dacf1a059a` used bosn's
source-bound act2 engine and completed both selected jobs successfully. An
unchanged retry reused the attestation without rerunning the workflow.
The `ci-full` label preserves full validation of the shared runtime change;
remote verification and Dylint passed. Full run `37099060991` subsequently
failed: four tests reported missing ELF build identities and the blocked-stack
unwind test failed. An isolated probe with managed LLVM 21.1.5 showed that
Clang/lld omits GCC's default GNU build ID; explicit `--build-id=sha1` restores
it. The package build script now requests that identity for Linux target
artifacts without changing dependency Rust flags. The updated minimal gate
passed in 208 seconds, stamping `811cb58ddae0`; actual full Linux and native
platform validation remain pending. The C probe alone does not prove the
failing Rust tests pass. No trusted remote skip is enabled.

The next shadow rollout declared both local lanes and passed them together in
730 seconds on the earlier tree: Linux 253 seconds, Dylint 477 seconds,
stamped `be12a9b4b396` for tree `61d78494dc67`. An unchanged retry required
no engine run. Its Dylint attestation explicitly names Linux x64:
Cargo `--all-targets` selects build-target kinds, not operating systems.
The branch is rebased onto the build-ID correction and undergoing fresh
validation; the earlier attestation does not prove the rebased tree.

A failed workflow-mode guard cost 140 seconds before the initial two-lane
attempt stopped. The lane now runs its fast Python guards before submitting
the engine; 159 guards completed in 0.523 seconds, and a regression proves
a failed preflight prevents engine submission. This is an observed local
optimization, not a new mechanically enforced fleet rule.

The existing minimal Dylint job also passed locally: run
`8f3fadaa-a7ac-43bc-b8ee-3e7d00093df6`, 500.9 seconds total, both selected
jobs successful, including the lint libraries' own tests and the all-feature,
all-target workspace pass. This is an execution measurement, not a stamped
Dylint lane yet. Its PR base was `main` at
`0aed1aba4560429e544e65bf37790909938ca614`.

The first Dylint attempt exposed an evidence defect: the recovered checkout's
`origin/HEAD` pointed at the feature branch, so bosn used that same commit as
the PR base and diff-gated lint-library tests were skipped. That attempt was
cancelled and its engine removed; it is not successful coverage evidence.
`git remote set-head origin -a` corrected the checkout. The next rollout
branch rejects any default-base ref other than `origin/main` before submitting
the lane; its focused regressions failed before implementation and passed
afterward. A correct source SHA alone does not prove diff-selected coverage:
inspect the event base and the executed commands too.

New local commits remain expensive because the ci-lint planner's PR
`cache_save=false` is forwarded to setup-soldr's global `save-cache` input,
which correctly disables local saves too. The shared correction is
[setup-soldr PR #565](https://github.com/zackees/setup-soldr/pull/565): an
optional remote-only write policy preserves planned remote restrictions while
the action retains its own act-aware local automatic saves. Explicit global
disable remains authoritative. Defaults preserve existing consumers.
Nineteen focused policy tests, four archive-wrapper pretests, 894 full-suite
tests (one intentional watchdog skip), typechecking and action bundling passed.
The first remote Python contract failed because its exact public-input
registry had not been included in the local validation; after reproducing the
failure and adding the new input to that exact set, all 44 contract tests
passed locally. This is a first-push miss, not a 100% success claim.
Review passed and all 49 remote checks passed. The action merged as
`d17a58eb2cea17a89af0824fb7c6b24ee68f5083`. Its required exact-SHA full
canary is [fbuild PR #1634](https://github.com/FastLED/fbuild/pull/1634);
`v0` promotion and consumer adoption remain pending that evidence. A PR-opened
event superseded the first labeled canary and selected minimal coverage;
reapplying `ci-full` produced run `37101793881`, whose `full / verify` passed.
Only successful selected/full coverage plus logs proving execution of the
exact action SHA qualify for promotion. Cold/new-commit/warm timings after
adoption remain unmeasured.

## Shared runner fix: merged and measured

[Bosn PR #441](https://github.com/zackees/bosn/pull/441) merged as
`6ae32cd7dca8947af69863cd1df12fca7779c40a`. Its full local gate passed in
696 seconds: unchanged Python/static and policy lanes reused their earlier
passes, Rust completed in 80 seconds, and isolated native packaging plus Python
and Rust tests completed in 616 seconds. The Python suite passed 248 tests
with 10 Docker-marked tests deselected; existing ignored Rust Docker tests
remained ignored. This gate does not establish full Docker-test coverage.
An unchanged-tree retry reused all four lane keys and reported 0 seconds,
stamping the same tree `7a8503506e98979b8cab53d6d4c080786562a246`.
[Remote CI](https://github.com/zackees/bosn/actions/runs/37096813519) passed
every selected check, including the attestation verifier and locked Rust tests;
full-tier hosted macOS and widget jobs were skipped as designed, so this is
ordinary-PR evidence, not a full-release validation.

Kernal-api candidate run `9562187d-8294-421d-a418-2efb52ac537e` executed
the exact clean source `414cad28634170c2859f12adee94bfcd80d821f2` through
the newly built bosn in a separate daemon state directory, using act2
`0.2.89-act2.2`. The previously failing CI-guard step passed in 1.59 seconds
(131 Python guards plus shell and PowerShell scripts). It reached compilation,
then failed at default-facade Clippy: Soldr 0.9.23's linker shim invoked
`clang`, which the pinned slim image lacks (GCC is present). The run finished
failed and cleanup removed its engine; no local attestation was stamped.

Released Soldr 0.9.29 includes the existing upstream managed-Clang resolver
(soldr#3430), so the kernal-api candidate now pins that one runtime through
its shared wrapper. The version guard failed before the wrapper change, then
all 43 focused cache/local-gate/native-job guards passed. Run
`7073d525-ebbf-4f3b-94fa-2c3c640336b4` passed on source
`d0e824bb1f957e5495bb76742a87c79ceb517afd`: both selected jobs succeeded,
with act execution 219.6 seconds and total 250.5 seconds; cleanup removed the
engine. Warm workflow reuse still needs runtime evidence. Its new canonical attestation
definition names only checks the minimal Linux lane performs and maps no remote
jobs. Other native platforms, Dylint and full-feature coverage remain required
for the complete migration.

Review then required an explicit native Linux x64 process-host and Docker-server
architecture check, and uv invocation in the remote verifier. Those fixes passed
45 focused guards and received a clean review. Declared local-gate run
`f52f36c3-0e26-4ca6-a3b4-57b4a40e9888` passed in 285 seconds on source
`b9b9abe4c882449624524e59b0b788083b6709f7` and stamped its tree. A static
LAYOUT-001 check then required moving Python host queries into the exact
declared CI facade `ci/platform_host.py`; that check and review now pass.
Run `6e6f9a6d-9ff4-49fd-b3a5-9f3eaceac183` is validating the resulting
source `48611063be0361fb302fc74e3e7fd2b2ca9e8188` before publication.
The gate uses the built bosn wheel through an isolated uv environment and the
separate `BOSN_STATE_DIR`; the wheel CLI's SHA256 matches the tested native
binary, `c3bcaa01b78bac52d0c63d72639d9ab0886c90d7c15a124d3a7437d46f85bc9a`.

The notes below retain earlier checkpoints; the section above supersedes their
pending statuses for the shared runner fix and first successful kernal-api run.

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

Canonical attestation-file inventory rechecked through the GitHub Contents API
on 2026-10-03 at 04:23 UTC:

| Repository | Default-branch `ci-attestations.yml` | Observed blob SHA |
| --- | --- | --- |
| zackees/zccache | Present | `19dfe90106c5087c86e1ae18d6b6b449194cb5b4` |
| zackees/soldr | Present | `ab5354c4f7ade0365e455d59476d22b10d0cba92` |
| FastLED/fbuild | Missing (HTTP 404) | — |
| zackees/bosn | Present | `d700fdbdcf511c7ab740e9282f171a8a1ba37941` |
| zackees/clud | Present | `c0254c7b744f1a40ab6b25351ada1dbfc752e087` |
| zackees/kernal-api | Missing (HTTP 404) | — |
| zackees/running-process | Missing (HTTP 404) | — |

This inventories the canonical root file only. Presence does not establish
gate fidelity, attestation validity, warm-cache behavior, or remote skip wiring.

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
