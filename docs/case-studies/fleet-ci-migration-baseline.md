# Fleet CI migration baseline — 2026-10-03 UTC

## Fbuild enforcement merged; warm remote execution measured

[Fbuild #1636](https://github.com/FastLED/fbuild/pull/1636) merged at
`8246c3099085` after [latest-head ci-minimal 37116500210](https://github.com/FastLED/fbuild/actions/runs/37116500210)
passed both Ubuntu jobs, all three selected boards and the coverage aggregator,
and [Dylint 37116499972](https://github.com/FastLED/fbuild/actions/runs/37116499972)
passed the all-OS-target check. All ordinary static and board-definition checks
passed. Extended/full labeled coverage was not invoked. This initial rollout
is shadow mode, so it does not establish enforced pre-push validation or a
100% first-push rate.

[Fbuild #1637](https://github.com/FastLED/fbuild/pull/1637) follows from that
exact merged source. It changes the declared mode to enforce and confines
PR attestation verification to PR events. The local gate uses the existing
workflow-dispatch event to execute the same required Ubuntu commands before
stamping; its frozen run proof requires that event. Default-branch pushes and
dispatch keep a successful verifier dependency. Twenty-six focused checks,
Ruff and code review passed. Published Bosn 0.1.11 ran the full declared gate
in 224 seconds (`963a50d7-4f74-4fed-ace2-3cfaf2820bad`), attesting
`d962b457cce9` / tree `4d5064a4071a`. Real PR verification rejected the
unsigned head (exit 1), then accepted the stamped head. Exact-head
[ci-minimal 37117558797](https://github.com/FastLED/fbuild/actions/runs/37117558797)
and [Dylint 37117558377](https://github.com/FastLED/fbuild/actions/runs/37117558377)
passed before #1637 merged at `971efdf2bc83`. Its single pushed head passed
on the first attempt; that individual result is not a fleet-wide 100% rate.
Board, extended/full and other native-host coverage remain remote; broader
local coverage and quick-gate reuse are still required work.

The merged default-branch [warm run 37118394155](https://github.com/FastLED/fbuild/actions/runs/37118394155)
passed in 261 seconds overall. The workspace job took 241 seconds, with
Clippy taking 59 seconds (Cargo 45.91) and Test taking 147 seconds (Cargo
compilation 97.12). The build layer reported an exact hit on the healthy
main seed saved by the preceding merged run; the target layer used a
restore-key fallback. The preceding PR workspace job took 787 seconds,
with Clippy/Test compilation of 342.90/359.48 seconds and a build miss.
That PR started before the main seed was saved. These are observed cold-PR
and warm-main contexts, not a controlled universal speed ratio or a cache
hit rate inferred from cumulative zccache statistics. Linked tests still
rebuild; no forbidden test-binary cache was introduced.

The next-candidate [running-process first-push refresh](running-process-first-pass-2026-10-03.json)
reports **0 of 4** merged PRs since 2026-10-02, with no attestations. The current
repository still has no `ci-attestations.yml` (HTTP 404). Its existing Bosn
configuration alone does not establish the requested workflow migration.
This refresh supplements the earlier 0/3 sample below; it does not replace
historical mixed-run rates or claim that recent default-branch CI is all red.

## Running-process unchanged baseline: shared runner parity failure

[Migration issue #1306](https://github.com/zackees/running-process/issues/1306)
records the unchanged `9b4a5c656de9` quick lane under published Bosn 0.1.11
and act2.2. Local run `54c2130f-30e5-4eb2-bf9b-19ea37658ce7` completed in
621.223 seconds: Dylint passed, preflight failed in Unit Tests, and lint-gates
correctly failed. Nextest stopped after 1508 of 2346 tests, reporting 1507
passed (one flaky), one failed and nine skipped; earlier failures retried.
This is neither complete test coverage nor a passing local attestation.

The ordinary-process privilege test expected no privilege but observed
`Some(UnixRoot)`. A read-only-directory loader fallback also wrote where an
ordinary user should be denied. The pinned runner image declares root and
act2 inherits it for hosted-VM simulation. A focused stock-image probe
confirmed UID 0 and a successful write into a mode-0500 directory, exiting 1
on the required non-root assertion. [act2 #9](https://github.com/zackees/act2/issues/9)
tracks a shared fix with writable home/workspace/tool-cache and explicit
container-job regression coverage. Other APE and process-tree failures
remain unresolved; restoring user parity must precede attributing or
weakening any product test. The separate hosted-VM umask proposal remains
unresolved in closed act2 PR #6.

[Act2 PR #10](https://github.com/zackees/act2/pull/10), at
`4f4888fc0847369805f65c253a0f943848a32770`, now passes ordinary shell/Node
identity, denied writes, real checkout/setup-python/setup-uv actions, an
explicit root container, a root-owned completed tool-cache seed, and
bound-checkout ownership checks. Guards cover HOME, option-derived and
account/sudo/mail mounts. Hosted checkouts have a private writable parent;
generated-script directories belong to the ordinary runner. Native Docker
actions retain their original destination mapping, and hosted networking
fixtures use sudo with the stock Ubuntu image.

The final local `go test ./... -skip '^TestRunDifferentArchitecture$'
-count=1 -timeout=20m` passed, with the runner package taking 407.706
seconds. The excluded ARM-emulation case is unsupported on this host;
the existing remote QEMU check remains required. The normal CLI action
smoke, artifact upload/download, short tests, Go vet, golangci-lint (zero
findings), and WITHOUT_DOCKER build also pass. Earlier broader failures
were resolved through workspace/generated-directory ownership, hosted
fixture privilege commands, private-harness Git/loopback/image visibility,
and native Docker-action mapping. Review is clean.

The first remote run on `564233d` passed the runner package including QEMU
in 523.534 seconds, but 14 new container fixtures failed because they assumed
the pinned image was preloaded. Explicit image pulls made the fixtures pass
against an initially empty owned engine in 30.132 seconds. Every required
[remote check on `924e247`](https://github.com/zackees/act2/actions/runs/37127190934)
then passed, including Linux/QEMU, native macOS/Windows, lint and snapshot.
A further focused probe found Docker socket access denied for the ordinary
user. The final addition grants membership in the socket's existing group
through private account files; the bound UID 1000 regression verifies the
socket's owner/group/mode remains unchanged and Docker access works. The
normal CLI smoke now includes Docker access. The next
[Linux run on `8b3d4f3`](https://github.com/zackees/act2/actions/runs/37128566156)
panicked in the user-inclusion matrix test. A local race-enabled run reproduced
concurrent reads and writes of the default socket path in shared configuration;
both job and service mount builders now resolve that default locally. A
deterministic regression failed before the fix, and ten race-enabled matrix
and mount-regression repetitions passed afterward in 18.319 seconds. Final-head
checks are [running](https://github.com/zackees/act2/actions/runs/37130101405). The
candidate remains draft, unmerged and unreleased; no running-process
attestation has been issued. These milestones do not erase the first-push
failure or replace the running-process sample.

The post action reported a build miss with save skipped by global
`save-cache` policy. An unpushed focused regression is RED on the original
main-only global permission and GREEN when global `auto` is separated from
remote writer permission. Failed-build versus pure-test-failure saves,
Dylint delegation, permitted cache payloads and source-bound verifier
wiring still require implementation and real successful gate evidence.

## Failed Fbuild cache seed: caller status wiring proven

The unchanged-main local run `6bd1f21d-9c65-4737-b779-a4e63f587502` failed the
workspace test **link**, yet setup-soldr saved its build cache (`id=5`) and
target cache (`id=6`). The pinned action at `d17a58eb2cea` has the
setup-soldr#559 failed-job save gate, but its empty `job-status` input preserves
legacy always-save behavior. Fbuild's two Ubuntu calls omitted that input.
Later successful runs restored the failed job's immutable build-cache seed
as an exact hit and could not update that generation with newly compiled
content. This is evidence of an incomplete failed-build seed, not evidence
that the cache's existing compiler objects are corrupt.

The published Fbuild correction `f56be5161729` passes `${{ job.status }}` to
both calls and rotates only the workspace generation to
`check-ubuntu-py312-v1`, retaining the facade job's valid existing cache.
Twenty-one focused tests passed after a regression first failed on the
missing input; code review passed. A private negative fixture compiled a
small real Rust crate, exited 42, and completed as failure in local run
`d1ef2cf2-1202-421d-be19-47f1b9a54100`: both build and target layers reported
`failed-job-skip`. The fixture is not published or attested as a passing
gate. The correction passed its declared positive gate in 583 seconds
(run `042053f9-07ad-40af-8e87-2a3dd1aeee26`) before push. Its workspace
build-cache miss saved a fresh seed as `id=10`. A forced engine warm run
`075544e9-0d2d-4886-b1b9-0eeb3c12d40c` restored that generation as an exact
hit and passed both required jobs in 247 seconds. Workspace test-profile
compilation fell from 140.98 seconds on the preceding generation to 95.76
seconds; linked test binaries still rebuild. The warm run attested the same
source tree `ddcfc5246492`, amending only trailers to `06c913d88bb7` locally.
The published head remains `f56be5161729` while its remote checks run.

The subsequent correction separates a completed assertion failure from a
failed compilation without swallowing the test failure. `ci/test_cache_status.py`
streams the same `soldr cargo test` selection with JSON compiler evidence and
preserves its exit code. Only a successful `build-finished`, a failed libtest
summary and Cargo's test-failure diagnostic, without compiler errors, permit
`save-on-failure`; job cancellation never opts in. Twenty-four focused tests
and code review passed. Real Rust assertion/compilation probes both returned
101, with the save opt-in true/false respectively. Private workflow run
`520a2bfd-58e3-478b-b71f-351fb8d4faed` remained failed on its assertion while
setup-soldr actually saved build `id=12` and target `id=13`. The fixture is
unpublished. The full positive gate through published Bosn passed in 238 seconds
(`e694c2e1-86a0-4f60-84ec-1cfceaf51aa8`), attesting tree `8335396bc6a8` at
`a4512c014c51`. Remote verification of this correction passed and #1636 merged; see the rollout above.
This is repository wiring for existing `CACHE-008`, not a new central checker.
Further speed work must reuse permitted compilation inputs/intermediates:
`CACHE-007` forbids linked test binaries, incremental directories and whole
target trees in cross-run caches or persistent stores. A full target
snapshot is not an acceptable shortcut around the measured compile cost.

Bosn's runner-tools version PR
[#442](https://github.com/zackees/bosn/pull/442) merged at `bacf665de6a1`.
[Exact-SHA full CI 37114032741](https://github.com/zackees/bosn/actions/runs/37114032741)
passed every required cell, including Docker tests and both hosted macOS
wheel smokes. Release dry-run
[37114605896](https://github.com/zackees/bosn/actions/runs/37114605896) passed.
[Publication 37115034048](https://github.com/zackees/bosn/actions/runs/37115034048)
passed PyPI and GitHub publication. The [v0.1.11 release](https://github.com/zackees/bosn/releases/tag/v0.1.11)
identifies that exact merged SHA and contains four platform wheels. Installing
`bosn==0.1.11` from PyPI into an isolated Python 3.12 environment succeeded;
the installed CLI reports `bosn 0.1.11`. The forced gate using this installed release passed in 245 seconds
(`3f5c046c-bfb3-4030-b5d9-7073f92a362a`), independently of the source-built
candidate. Corrected-head remote CI `37114988442` and all-target Dylint
`37114988158` passed before the subsequent cache-outcome correction.

## Fbuild migration: preceding shadow proof and timing baseline

The first remote run of [Fbuild draft PR #1636](https://github.com/FastLED/fbuild/pull/1636)
is green: [ci-minimal run 37112762596](https://github.com/FastLED/fbuild/actions/runs/37112762596)
completed the shadow verifier, both Ubuntu jobs, shared fbuild binary,
Arduino Uno, ESP32 and Teensy builds, and `CI selected coverage` successfully.
[Dylint run 37112761945](https://github.com/FastLED/fbuild/actions/runs/37112761945)
also passed the full all-OS-target pass. Every ordinary static and
board-definition workflow passed. Extended/full labeled coverage was not
invoked. The PR remains draft while the corrected head and released runner are validated.

The [local job-section timings](fbuild-local-runtime-timings-2026-10-03.json)
locate the warm execution cost: workspace `Test`
took 189.5 seconds, including roughly 140 seconds of test-profile compilation;
Clippy took 49.6 seconds and setup-soldr 6.2 seconds. The facade test step
took 67.4 seconds. Reusing permitted compiler intermediates is the
larger optimization opportunity; reducing setup cannot explain or remove the
140-second compile. These are runtime step timings, not inferred cache hit
rates or a claim that every test execution is fast.

[Fbuild draft PR #1636](https://github.com/FastLED/fbuild/pull/1636) carries
the source-bound shadow gate. Its tree `97ec665f5af3` passed the declared
local gate in 284 seconds (run `3bf730c2-b0c1-4ce4-baa5-74a21cfb015a`), then
a forced engine execution of the same tree in 286 seconds (run
`f4da150c-634c-40be-a700-0781565d5998`). Both runs completed the verifier,
workspace job and ignored Python-facade job successfully. The final commit
`c14693b5bd06` carries four gate attestations. A forced local-gate invocation
with lane reuse enabled finished in 0 seconds over the same 3,364 inputs,
without starting an engine; this is distinct from warm engine execution.

The runtime warm pass is not faster than the first successful pass: thin
target-cache fallback still rebuilds workspace leaf crates. The build-cache
exact hit skips saving newer content, while the target layer saved the first
successful workspace run. Reported zccache session totals are global
last-writer-wins fallback data with unknown originating workspace, so they
are not reliable evidence of a complete run's hit rate.

Local evidence used a wheel built from bosn's merged stock-runner-tools fix
`6ae32cd7dca8` ([bosn PR #441](https://github.com/zackees/bosn/pull/441)).
Published bosn `v0.1.10` at `189e5e8d9840` predates that fix. Runner dependency
publication is now complete at v0.1.11; validation with that installed release
and the corrected remote head remains required before rollout. The fleet
migration and its 100% first-push target are not complete.

[Fbuild issue #1635](https://github.com/FastLED/fbuild/issues/1635) owns the
next source-bound bosn/act2 migration. Its unchanged-main baseline at
`6acb66a2f54f`, local run `6bd1f21d-9c65-4737-b779-a4e63f587502`, took
572.7 seconds and failed the workspace test link: `rust-lld` could not find
`libpython3.12`. The separate ignored Python-facade job passed. Ignoring the
embedded-CPython tests does not remove their test binary's link dependency.

The local draft `a4b258b8a028` selects uv-managed Python 3.12 explicitly for
the workspace job, preserving its hosted interpreter version and every
Clippy/test command. The facade job keeps Python 3.10 and its distinct cache
suffix. The draft requires successful evidence for both `check` and
`python-facade-tests` on the same clean source through bosn's act2 engine;
dirty, stale, partial, skipped or malformed evidence fails closed. Its
verifier is shadow-only; board, extended/full and other native-host coverage
remain remote. Only existing workflow files are changed, with the minimal
entrypoint generated from `ci/render_workflows.py`.

Nineteen focused tests and the code review passed. The broader guard suite's
cache-key uniqueness failure reproduces on unchanged main and is not waived.
Parsed local-gate lint also reports existing standalone-workflow verification,
bare Rust-tool and CodeRabbit findings, plus an undeclared mirrored-job
relationship. The new verifier's missing dependency was corrected in the
generator. The successful local runs above establish Linux proof fidelity;
remote validation and faster warm engine execution remain outstanding.

## Latest validation: nightly cache reuse proven; kernel permission correction pending

The unpushed kernal-api cache-policy tree `5839aa5acbc5` moves the unconditional
source-wide platform-boundary scan into the existing Dylint lane, where
setup-soldr already prepares the matching nightly. Its test scope stays
`--locked --lib -- --skip ui`; the workspace Dylint pass still checks all
features and target kinds. The attestation names the source scan explicitly.
All 162 fast guards passed in 0.526 seconds, and review was clean.

Local run `f32f9be5-9910-4de4-894d-098df8af653c` passed in 444.5 seconds and
saved the Dylint foundation and output layers as cache IDs 7 and 8. A fresh
engine on the same committed tree, run `2f246faa-18a4-4346-bb8d-f7c8ccadeb5c`,
restored both exact keys, passed in 207.3 seconds, and skipped redundant saves
because of those exact hits. That is a 53% reduction in total local duration;
the engine itself took 434.6 then 167.7 seconds. The warm source scan passed
21 tests in 25.6 seconds including compilation. Both runs also executed the
lint libraries' own tests. These are successful Dylint-lane timings, not a
passing combined Linux-and-Dylint gate or proof of other native platforms.

[Act2 #8](https://github.com/zackees/act2/pull/8) passed every remote check in
run `37108887269` and merged as `b7edfa48520c`. Its platform-aware image lookup
avoids unnecessary pulls on Docker's containerd store. The permission
correction was rebased locally onto that fix. Its complete local Go suite
passed every test except `TestRunDifferentArchitecture`, which also fails
on unchanged master because this machine lacks a host binfmt registration.
The nested host Docker-action fixture now passes after placing its temporary
workspace in a cache path visible to the daemon (1.104 seconds). No product
or consumer assertion was weakened. The permission PR remains closed pending
clarification; it is not merged, released or pinned by bosn.

The refreshed [kernal-api first-push sample](kernal-first-pass-2026-10-03.json)
contains 11 merged PRs since October 2 UTC: 5 first-pass successes (45.5%).
Migration PR #399 needed a full-CI correction despite its narrower local
attestation; shadow PR #400 passed first push. Two attested PRs do not prove
100% reliability, and the sample's explicitly requested target remains 100%.

## Earlier checkpoint: kernel shadow rollout merged; shared runner corrections pending

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
An initial workflow fixture passed in 9.801 seconds: hosted runner directories
and files were `755` and `644`. Docker's native mask varies between daemons;
the corrected live regression compares an explicit container with direct
Docker execution instead of assuming `0000` universally.

The first remote run `37106193075` failed Linux: the proposed `/bin/sh`
wrapper dropped hyphenated action-input environment names, breaking stock
artifact and composite actions. This is a real first-push regression.
A live environment regression reproduced it before correction to
`bash --noprofile --norc -p`, which preserves those names and ignores shell
bootstrap hooks. All affected runner fixtures then passed locally in
31.987 seconds; the full artifact package passed in 102.131 seconds.
The complete local suite is not green: three remaining failures reproduced
on unchanged act2 base `d3f33ec` in the same nested harness. The Git package
passed in 72.055 seconds after configuring its test author identity.

[Act2 #7](https://github.com/zackees/act2/issues/7) tracks the independently
reproduced containerd image-store lookup bug: inspecting a multi-platform tag
without a platform returns its default architecture, falsely reporting a
locally present ARM variant absent. [Act2 PR #8](https://github.com/zackees/act2/pull/8)
turns that live regression green in 4.472 seconds and passes the same test
against an isolated Docker 28.5.2 classic/vfs store in 23.035 seconds.
It covers missing variants, server errors, ignored query parameters and
older APIs; lint, vet and review passed. Native execution and QEMU availability still need
separate proof; image presence alone proves neither.

[Act2 PR #6](https://github.com/zackees/act2/pull/6) was closed by the owner's
account without a comment while its first checks ran. The correction is
pushed, but reopening awaits clarification of that external closure.
Neither shared fix is yet merged, released or pinned by bosn, and the kernel
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
