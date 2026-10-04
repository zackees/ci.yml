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
checks [passed](https://github.com/zackees/act2/actions/runs/37130101405), including
Linux/QEMU in 11m42s, native macOS/Windows, lint and snapshot. PR #10 merged as
`5af1c449274d7835ad7a26aee1ed376b3e6a7e09`; its tree exactly matches the tested
head (`e087757816d04327caee08d465464b1c7bf0a3b0`).
[Release v0.2.89-act2.3](https://github.com/zackees/act2/releases/tag/v0.2.89-act2.3)
published through the existing tag workflow. The downloaded Linux x86_64
archive matches its published checksum; its extracted binary reports the
expected version. Bosn 0.1.12 pins both verified digests. Its focused isolated
engine/pin/tool tests pass after a stale-URL regression failed, and its full
local gate passed in 586 seconds. [PR #443](https://github.com/zackees/bosn/pull/443)
merged as `08c7c8cdb262b980d56fd9fccbe9b6156fc6ec82`, with the exact tested tree.
The [exact-main full CI](https://github.com/zackees/bosn/actions/runs/37132565941),
[dry release](https://github.com/zackees/bosn/actions/runs/37133268857), and
[publication](https://github.com/zackees/bosn/actions/runs/37133859473) passed,
including all four wheels and both native macOS smokes.
[Version 0.1.12](https://github.com/zackees/bosn/releases/tag/v0.1.12) was installed
from PyPI into a private environment and reports the expected version.

Before publication, the source-built candidate ran the unchanged running-process
Linux quick gate against the same source snapshot and event payload. It failed
in 686.285 seconds: the ordinary-user and read-only loader fallback tests passed,
Dylint passed, and nextest executed 1549 of 2346 tests (1545 passed, including
one retry; four APE tests failed). The unexecuted remainder prevents attestation.
A separate diagnostic using the released act2.3 binary observed umask `0000`
and default directory mode `0777`; a diagnostic-only `0022` control produced
`0755` and passed the directory-permission assertion. At that stage this isolated a runner
permission gap, but did not prove that all four APE failures shared that cause.
No production umask change or repository workaround was applied. The published-wheel run `675e97ac-ce35-4b7c-abe8-db9bb7416ea3`
then failed in 920.297 seconds against the identical snapshot and payload.
Dylint and both targeted permission/fallback tests passed. Nextest executed
1531/2346 tests: 1530 passed (one flaky), one final APE nested-spawn failure,
nine skipped. The other three APE cases failed earlier attempts, but fail-fast
interrupted their remaining retries; this does not prove they passed. The
aggregate correctly failed. No running-process attestation has been issued. A subsequent controlled diagnostic on the same source and released
act2.3 ran the entire Linux APE test module: default umask `0000` produced
14 passes and exactly the four APE failures above; a diagnostic-only `0022`
step reused the same source/build and passed all 18 tests, with zero failures
or ignored tests (202 unrelated tests filtered in both steps). The fixture
asserted baseline failure and control success. Its initial checkout attempt
stopped before testing because a token was required; the corrected fixture
fetched public Git source and checked the exact SHA. This is stronger causal
evidence for the runner permission mismatch, not a successful full gate.
[The umask PR remains closed](https://github.com/zackees/act2/pull/6), awaiting
clarification of its closure; no production change or repository workaround
was applied. These milestones do not erase failed attempts or replace its
first-push sample.

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


## Clud rollout evidence after the initial snapshot

Clud's current source has a one-lane `bosn ci run` gate, seven per-gate
attestations, and enforced trusted skips for the five routine Linux job
groups. Its initial `19/34`, `0/34` trailer snapshot below predates this
verified rollout example; it remains historical evidence.

[PR #1782](https://github.com/zackees/clud/pull/1782) merged after its
[remote run](https://github.com/zackees/clud/actions/runs/37110407536) passed
in about 20 seconds. The exact head `7994e15ab06abed5ea9ce73d241f9ffea5a2b965`
contains a `Local-Gate:` trailer for its actual tree
`a23469681577136f11a81c9d6ae21569df674f57`, with a recorded local duration
of 473 seconds and seven `Ci-Attestation:` trailers. The remote log confirms
`GATE-003` accepted the tree, `GATE-008` trusted the unchanged gate surfaces,
and `GATE-010` authorized all five skips. `CI OK` succeeded. The three Dylint
target attestations are Linux-hosted checks, not native Windows/macOS tests.
This example proves the trusted-skip path works; it does not establish a
100% first-push rate or independently verify every local test from trailers.

An unchanged current-source baseline on `89b7fb7214fd43836ee34c7d7c985059a873dd6f`
passed with the privately installed PyPI Bosn 0.1.12 wheel and released
act2.3 (`3789d32d-feea-4e75-9d16-2e601c06d86e`) in 1007.548 seconds.
Static checks, Clippy, build, all three Dylint targets, all three unit shards
and `CI OK` executed successfully. The expanded unit matrix children passed;
the unexpanded planning node reports skipped. Existing ignored/skipped tests
and opt-in tiers were retained, so this proves the selected routine plan,
not the entire repository test graph or native Windows/macOS coverage.
The fresh runner state contains compile misses, so this is a cold baseline.
No Clud source, test selection, cache-writer arrangement or coverage changed.

The [refreshed strict first-push scan](clud-first-pass-2026-10-03.json), using
the same start date and workflow, found 36/55 (65.5%) first-pass PRs, with
trailers on 3/55; all three attested PRs passed first push. Trailer presence
alone remains weaker than the exact-tree acceptance verified above. Sparse
adoption and the historical failures mean this does not meet the fleet's
100% target. The earlier denominator and failures are preserved below.

## Zccache Bosn Actions migration

At the unchanged baseline `b7ccf9f5e4814fc02b69f7d628ec62fad31ec60e`, Zccache has
attestation and trusted-skip declarations, but its isolated test lane
uses `bosn run --task gate-test`, rather than the Actions plan through act2.
Its workflow/cache guard explicitly allows ACT-specific save overrides.
This is a remaining migration target, not evidence that a manifest is missing.
No Zccache edits had been made at that baseline.

[Issue #1885](https://github.com/zackees/zccache/issues/1885) records successful
published Bosn 0.1.12 / act2.3 baselines: the actual Integration workflow
passed in 499.122 seconds, and MSRV with its dependencies passed in
147.414 seconds. Integration ran 3478 nextest cases successfully, with 286
existing skips; MSRV ran six nested Dylint cache-contract tests. These are
selected Linux PR jobs, not the native or schedule-only test graph.

[PR #1886](https://github.com/zackees/zccache/pull/1886) replaces the gate's
check/tests lanes with those actual Actions definitions. Its frozen typed
receipt checks bind the workspace, clean HEAD, workflow/job selection,
terminal success and successful required Main steps. Host/daemon architecture
checks reject unsupported or unknown test fidelity. The existing nextest
host-refusal predicate and other remote/native coverage remain intact.
The ci-lint pin advances to existing Bosn receipt support, and no workflow
file is added. ACT-specific cache overrides remain a separate follow-up.

The committed-tree gate passed in 637 seconds: lint 46, CI-helper tests 33,
MSRV 138, documentation 32 and Integration 387. Actual Bosn runs
`51fca9ce-803d-4e5f-9e32-5a649c003464` and
`325ef59f-13fc-43ca-a5eb-c35833426c2c` executed against the clean candidate
`1248090a6ee2fe945e192d82165b73657675c42f`. The gate stamped head
`36f64f3a16ea36d16083c2fdcb503f449d539c22`, tree
`4664118014a09f6b82d51d60e201f015f9cee59a`, with six per-gate attestations.
Integration again passed all 3478 nextest cases, its doctests and explicit
contracts, retaining the 286 skips. The 36 focused proof/wiring/fidelity
cases, Ruff including C901, cache-footprint guard and primary review passed.

An unchanged-tree `--force --no-stamp` gate invocation reused every lane
and returned in about 0.5 seconds. This measures input-bound reuse of prior
successful checks, not another full suite execution or a performance ratio
for changed source. The initial host linker/daemon failures remain recorded
in #1885; neither received an attestation. The successful full retry used a
session-local Soldr root and Nix's native linker for host metadata checks,
leaving the incompatible shared daemon alone; product tests ran in Bosn.

The [remote CI run](https://github.com/zackees/zccache/actions/runs/37140388351)
accepted the exact tree (`GATE-003`). `GATE-008` correctly returned
`surface-changed` because this PR edits workflow definitions, so all four
covered jobs still run remotely. This confirms source proof and fail-closed
trust, not the later ordinary-PR skip path. [PR #1886](https://github.com/zackees/zccache/pull/1886) subsequently merged
at `95b8f604ed8e7b35973b100a9e798cbd33d5389a` after all nine triggered
workflows passed on the single pushed head: CI, Integration, Linux, macOS,
Windows, Python Tests, Filesystem Matrix, Wrapper end-to-end and Perf Guard.
The Windows x64/ARM Test jobs executed successfully. Existing intentional
skipped checks are preserved; no failed run was rerun or discarded. This
proves one successful attested rollout, not a fleet-wide 100% rate.

The [refreshed Zccache GATE-004 scan](zccache-first-pass-2026-10-03.json),
using the same start date and `ci.yml`, reports 10/14 (71.4%) under its strict
single-head criterion, with trailers on 4/14; all four attested PRs meet
that criterion. The four misses (#1868, #1872, #1874, #1876) have multiple
pushed heads and zero recorded failed runs or reruns. They are revisions,
not evidence of failed CI. GATE-004's binding single-head adoption definition
and default 80% threshold remain unchanged; the user's fleet target is 100%.
The old 5/9, 0/9 snapshot below remains historical. [Issue #270](https://github.com/zackees/ci.yml/issues/270)
tracks companion earliest-head pass/fail/unknown reporting so the adoption
metric is kept distinct from actual CI reliability. A single-workflow scan
also remains distinct from all nine rollout workflows above.

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

## Initial CI outcomes alongside single-head adoption (issue #270)

The [fresh companion ledger](fleet-first-head-2026-10-03.json) records 177
merged PRs across the seven repositories, since October 2 UTC. Its collector
is `670701f1b080020b3e5bba77b4eb80bbe692fcbe`. Each report preserves GATE-004's
existing single-head rate and adds the earliest observed workflow-run cohort's
pass/fail/unknown outcome, timestamp and run IDs. Same-head timestamp ties stay
together; later review revisions and duplicate triggers do not change the
initial outcome. A missing or overwritten first attempt remains unknown.

| Repository | Initial pass | Initial fail | Unknown | Pass rate among known outcomes | Evidence coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| zccache | 14 | 1 | 0 | 93.3% | 100.0% |
| soldr | 17 | 0 | 2 | 100.0% | 89.5% |
| FastLED/fbuild | 11 | 6 | 2 | 64.7% | 89.5% |
| bosn | 6 | 2 | 46 | 75.0% | 14.8% |
| clud | 33 | 9 | 13 | 78.6% | 76.4% |
| kernal-api | 3 | 2 | 6 | 60.0% | 45.5% |
| running-process | 1 | 0 | 3 | 100.0% | 25.0% |

A 100% rate with unknown outcomes does not establish 100% CI success. Bosn's
unknown count is especially material: for example, runs `36952870693` and
`36952871451` share the first timestamp of PR #328, with cancellation and
success respectively. That tied initial cohort remains unknown. These reports
cover `ci-minimal.yml` for fbuild and `ci.yml` elsewhere; they do not establish
all-workflow/native-lane success or reconstruct an earlier push without a
recorded workflow run. PRs without matching runs remain unknown in the
companion, while the original GATE-004 denominator still excludes them.

The same fresh scan's strict rates are 10/15 for zccache, 18/19 for soldr,
9/19 for fbuild, 41/54 for bosn, 36/55 for clud, 5/11 for kernal-api and 0/4 for
running-process. Old mixed-run and strict snapshots remain historical. The
policy target stays 80%; the migration goal remains 100%. The seven live
reads are not an atomic fleet snapshot, and this ledger predates the merge
of zccache's permission slice below.

## Zccache remote-permission rollout (#1888)

[PR #1888](https://github.com/zackees/zccache/pull/1888) merged at
`f277c97c5e644b5459c468d115a16c89b5066382`. All 23 existing writer steps now use
global `save-cache:auto` and the published remote permission guarded by main
push plus the writer barrier. Explicit global disables, cache families,
payloads, keys, budgets and job-status inputs remain unchanged. The footprint
guard also rejects remote PR overrides for independent build writers with
`cache:false`. No workflow file was added.

The final committed-tree gate passed in 725 seconds: lint 13s, CI helpers 38s,
MSRV 182s, docs 3s and Integration 489s. It stamped head
`10fb3bb1d4c988e9bbb04bc77e66e6ccb89319ec`, tree
`e883d31544ba00fb931b1116da34bcb0d8d6350e`. All ten triggered workflows passed
on that one pushed head, each on attempt 1: CI, Integration, Linux, Windows,
macOS, Python Tests, Filesystem Matrix, Wrapper end-to-end, Perf Guard and
Soldr Broker Stress. Remote GATE-003 accepted the exact tree; GATE-008 used
the workflow-surface-change fallback and retained remote execution. MSRV job
`111270602052` explicitly reports `save-cache-remote=false` for build, registry
and cook saves.

Local post steps reported build/registry exact-hit skips, rather than global
save-policy disables; they did not need to write a new payload. Integration
still compiles through a private seeded store while post state points at the
separate restored store. Publishing validated private outputs and resolving
immutable exact-key generations remain the next compile-reuse work. Failure
classification also remains separate: compile/cancellation failures must not
publish build outputs, while assertion-only failures may retain valid compiled
outputs. The permission rollout does not prove those follow-ups complete.

## Zccache isolated-store experiments (#1885)

[Issue #1867](https://github.com/zackees/zccache/issues/1867) already identified
why Linux Test could seed a cache yet lose its own workspace compiles:
first-writer-wins shared keys and post state pointing at a different store.
Its existing `publish-isolated-build-cache` action was reused for Integration;
this investigation did not require a new cache backend or ancestor resolver.

The initial clean candidate `3db3d6a12ec2c7a282f85e0ef53393328b555ee4` gave
Integration its own `integration` suffix and published the private store after
shutdown and audit. Published Bosn 0.1.12 / act2.3 produced these receipts:

| Run | Result | Integration prebuild | Full nextest result |
| --- | --- | --- | --- |
| `301118e4-c037-49c0-b96e-8ed768c0ae4b`, cold | Passed, 913s overall | 285s | 3,478 passed, 286 skipped |
| `131709c9-ea29-4852-8f4a-2f1a9eaac38c`, same source warm | Failed, 495s overall | 140s | 3,477 passed, one failed, 286 skipped |

The warm failure was the durability fixture's unchanged 500ms flush budget:
786ms was measured. It remains a failed gate, not successful warm-validation
or first-push evidence. The post step reported `failed-job-skip`; no
`save-on-failure` override or threshold increase was introduced. Phase-local
compiler rollups showed 49 hits / zero misses and 18 hits / zero misses for
the two prebuild commands. These are compiler-cache measurements, not a
claim that every harness or workflow was reused.

The initial publisher **wrote** a local build archive of 1,056,628,741 bytes.
But its suffix also split the setup cook key, whose post-build archive was
3,533,586,676 bytes. Both were measured with the published local compression
profile. This naive candidate was rejected before pushing: its new cook
archive alone exceeded the repository's 1.7GB cook-family allocation.

The revised candidate disables the setup cook for Integration, restores the
Cargo registry explicitly, and reuses dependencies and workspace units through
the isolated unit store. It preserves every existing build, nextest, doctest,
wrapper-contract, artifact-layout, ignored/stress and audit command. Explicit
`ci-tests:true` protects test-product policy; an explicit
`NEXTEST_TEST_THREADS:num-cpus` preserves parallel execution because that
setup input otherwise defaults nextest to one thread. Existing timing-test
slot reservations remain unchanged. The setting is documented by
[nextest](https://nexte.st/docs/running/).

The build-family allocation reserves 1.2GB for the measured store, increasing
that family's cap from 2.9GB to 4.1GB within the unchanged 9.5GB repository
cap. The pre-prune planner reserves the first Integration writer before any
entry exists, credits already-charged old-store bytes, and charges nothing
extra when a current-lock store exists. Both measured Linux Test and
Integration stores retire their old-lock generations before replacement.
The captured 247-entry listing totals 7,900,758,346 bytes; with the real
LF/CRLF lock hashes, its projected peak is 9,100,758,346 bytes under the
unchanged 9.2GB pre-prune target. This is a replay of that listing, not a
promise that later live inventories will fit. Historical inventories that
cannot also fit the additional producer correctly refuse writes.

A full gate on the intermediate no-cook candidate passed in 954s and stamped
`e587de8e8550706df463ca11b1c0643d22dc3eb7`. That run exposed the nextest
one-thread default. The final planner/parallelism revision passed its full gate
in 638s (lint 51s, CI-helper tests 38s, MSRV/Dylint 163s, docs 4s,
Integration 381s) and stamped `6f2cc5ace3de962746becdb0396d60dc74b00a36`,
tree `83ea03b0d81bf5532bdda8e137f2a1750a2e653c`. Integration receipt
`02c15449-a1de-4d7c-ad7f-a9e304e53153` records successful execution from
the clean committed source. Its prebuild took 123s; nextest took 80s with
3,478 passed and 286 skipped. Phase compiler rollups were 462, 103, 11 and
36 hits, each with zero misses. The seeder restored 4,462 artifact files
(4,437,966,101 bytes) into the private store; post steps reported an exact hit
and zero new compiles, so this final run did not write another build archive.
The 118 focused CI-helper tests and primary review also passed.

[PR #1889](https://github.com/zackees/zccache/pull/1889) publishes this
attested candidate and merged as `ec78c48fd5ccbf5914c007b5ff2ab800bc4e37a7`
after all nine triggered workflows passed on attempt 1 at head `6f2cc5ac`:
CI, Integration, Linux, Windows, macOS, Python Tests, Filesystem Matrix,
Wrapper end-to-end and Perf Guard. Broker Stress's existing path filter did
not select these CI-helper edits. CI run `37154697739` accepted the exact
GATE-003 tree and retained remote execution for the changed workflow surface.

Integration run `37154697662` took 394s overall; nextest passed all 3,478
tests in 75s, with 286 existing skips. The new profile seeded zero files;
the prebuild took 214s with 462 and 103 compiler-cache misses. The publisher
copied 5,068 files into the setup post store after audit, but build and
registry saves were correctly blocked by `save-cache-remote=false` in PR
context. This is successful cold remote validation, not a remote cache write.
Main push run `37155358402` also passed on attempt 1, with all 3,478
tests passing in 78s. The live pre-prune job `37155358159` reproduced the
9,100,758,346-byte forecast and retired the obsolete 316,908,722-byte Linux
Test entry before releasing writers. Integration's post step then saved
cache ID `8465515170`, key
`setup-soldr-buildcache-v2-linux-x64-63942eb6ab326045-integration-55c1323f88b8e77c`,
on `refs/heads/main`: 926,492,320 bytes, below the 1.2GB reservation.
The complete post-save listing contains 248 entries totaling 8,547,216,682
bytes, within the unchanged 9.5GB cap. This establishes first remote
publication. A deliberate same-tree dispatch, `37156033874`, completed its
warm prebuild in 98s versus 201s on the cold main run (about 51% less time);
its ordinary full-workspace test step also passed. Manual dispatch additionally
selects the existing ignored/stress suite, so its total duration is not a
comparable ordinary-run measurement. It restored 4,462 artifact files
(4,437,423,688 bytes); the two prebuild commands recorded 462 and 103 hits
with zero misses. The subsequent ordinary test and wrapper commands recorded
11 and 36 hits with zero misses. These compiler-unit measurements do not
claim linked test-harness reuse.

The diagnostic was deliberately cancelled during its additional ignored/stress
suite after the matched phases completed. Run `37156033874` retains its
`cancelled` outcome, and its post step reported `job status cancelled` and
skipped the build save. It is neither a successful full diagnostic nor a retry
or first-head outcome. All fourteen
workflows triggered by the merge push passed on attempt 1, including Coverage,
Clippy and Auto-Release. The deliberate warm dispatch is a separate diagnostic,
not a retry or first-head outcome. This successful rollout does not establish a fleet-wide 100% pass rate. Source-specific updates
under an already exact immutable key and assertion-only failure publication
remain separate work.


## Running-process private APE fixture (#1306)

Current main `9b4a5c656de959cc5c1f0317c16b8a520b8a8b07` still has no
local-gate or per-gate attestation manifest. Its published-runner baseline
failed in Linux APE cases under the runner's ambient `0000` umask. The
production loader already creates missing cache directories with mode `0700`
and rejects existing shared writable directories. The failing tests instead
passed an existing `tempfile::tempdir()` directly as their supposedly valid
cache; tempfile 3.27 defaults that directory to `0777` before applying umask.
That fixture depends on ambient permissions which its security contract does
not promise.

A test-only candidate, `782f6ae273170f18c21c2841f7f8678b4020a1bc`, creates
the Linux APE scratch fixture with `Builder::permissions(0700)` and adds a
regression assertion for its mode. Production code, ambient umask, and the
explicit shared-directory/read-only/symlink adversarial cases are unchanged.
This is a general fixture correction, not an act-specific permission override.
The closed act2 umask PR remains separate.

Native Linux x64, released act2.3 and the pinned stock runner image executed
immutable archives of the committed RED and candidate trees. With current
setup-soldr SHA `d17a58eb2cea17a89af0824fb7c6b24ee68f5083`, the RED source
passed 14 and failed five cases (the four APE failures plus the new private-mode
assertion); failed-job publication was skipped. The candidate passed all 19
focused APE cases under the same `0000` umask, including rejection of a shared
writable directory. This does not prove the full workspace or migration.

Two candidate diagnostics failed before testing with a rustup `EXDEV` rename
while adding clippy to a restored minimal toolchain. The harness initially
specified only a channel, and a subsequent unsupported components input did
not fix it. Pointing the documented `toolchain-file` input at the archived
repository's real manifest resolved clippy/rustfmt before restore and selected
a distinct complete-toolchain slot. No restored-tree permissions or contents
were modified to bypass the error.

An initial direct diagnostic also used a stale floating action ref from its
legacy action cache and wrote a retired solo-toolchain layer. It is not the
canonical cache-policy proof. The canonical diagnostic uses an immutable
current action SHA, explicitly disables that layer, and owns its separate local
cache. Published Bosn still passes `--use-new-action-cache`; removal of the
legacy switch and consolidation to one action-cache behavior remain candidate
ACT-002 work, not completed migration.

Published Bosn 0.1.12 run `90674329-00ad-495f-8489-d2f088018c32` failed
in 1,039.910 seconds on the clean candidate, using the actual unchanged Linux
quick selection: workflow `ci.yml`, job `linux-quick`, minimal PR mode, with
no label-based coverage additions or removals. All 2,347 nextest cases passed
in 56.226 seconds, including one configured retry and nine existing skips.
Python reported 825 passed, 26 skipped, 127 deselected and 94 subtests passed
in 102.05 seconds.

The subsequent real socket Hello performance gate failed: across 10,000
samples, P50 was 153.792 microseconds (budget 200 microseconds), while P99
was 1.120463 milliseconds (budget 1 millisecond). The aggregate stayed red;
no attestation was issued. The frozen latency budgets and test selection were
preserved. The cause of the latency failure remains unconfirmed; focused APE
success does not establish full routine validation, rollout or a first-head
or fleet-rate improvement. The attestation pilot remains pending.


## Evening fleet refresh and sampler shutdown candidate

The [2026-10-03 UTC refresh metadata](fleet-refresh-2026-10-03/metadata.json)
records the same merged-PR start date, October 2, using `ci-minimal.yml` for
fbuild and `ci.yml` elsewhere. The raw checker uses the policy's default 80%
threshold; the requested objective remains 100%. These are selected-workflow
measurements, not an all-workflow or required-check audit.

| Repository | GATE-004 single-head successes / sampled PRs | First-head pass / fail / unknown | Trailer-present first-head pass / fail / unknown |
| --- | --- | --- | --- |
| [zccache](fleet-refresh-2026-10-03/zccache.json) | 12/17 | 16 / 1 / 0 | 6 / 0 / 0 |
| [soldr](fleet-refresh-2026-10-03/soldr.json) | 20/21 | 19 / 0 / 2 | 19 / 0 / 2 |
| [fbuild](fleet-refresh-2026-10-03/fbuild.json) | 9/19 | 11 / 6 / 2 | 2 / 0 / 0 |
| [bosn](fleet-refresh-2026-10-03/bosn.json) | 42/56 | 8 / 2 / 46 | 5 / 0 / 9 |
| [clud](fleet-refresh-2026-10-03/clud.json) | 37/57 | 35 / 9 / 13 | 4 / 0 / 0 |
| [kernal-api](fleet-refresh-2026-10-03/kernal-api.json) | 7/14 | 5 / 2 / 7 | 2 / 1 / 2 |
| [running-process](fleet-refresh-2026-10-03/running-process.json) | 0/4 | 1 / 0 / 3 | 0 / 0 / 0 |

Unknown evidence remains unresolved. Trailer presence alone does not validate
an attestation. Historical failures remain in the cohort after a rollout;
these counts do not establish a fleet-wide 100% pass rate. The initially
mis-scoped fbuild `ci.yml` query found no matching runs and is excluded here.

Running-process remains the next pilot. Cache candidate
`325fde6e7335774e1ee519c0aa97aa8314dbf92f` permits successful local compiler-unit
publication, explicitly supplies `job-status`, selects `ci-tests`, and retains
main-only remote saves and nextest CPU parallelism. Its real Linux quick run
`cb458ed6-d386-454a-812f-6bbb52a396ec` failed in 1,204.406 seconds. The workspace
nextest pass ran 1,975/2,347 cases: 1,974 passed, one failed, nine skipped; fail-fast
cancelled the remaining cases. The guard-drop case failed all three configured
attempts at the unchanged five-second budget. Setup-soldr correctly skipped
build publication with `job status failure`; its target cache was disabled.
That proves failed-job save suppression, not a successful warm-cache cycle.

A queued-only attempt never executed and was explicitly cancelled. Restarting
only the owned private Bosn daemon restored admission. The unknown queue-stall
cause is tracked in [bosn#453](https://github.com/zackees/bosn/issues/453).

Separate native teardown instrumentation measured worker shutdown at 149
microseconds and crash-sampler shutdown at 1.40 seconds. Under an 8-CPU quota,
24 concurrent unchanged guard cases failed 21/24: worker shutdown stayed below
23 milliseconds, while sampler shutdown took about five to seven seconds.
This contention diagnostic initially omitted the workflow's GNU build-id flags;
it is not an exact replay of the full workflow.

Candidate `d8f4b457f525b0be63b3dde0e562bb4ef075474e` adds cancellation after
native capture resumes all siblings, between module/frame stages, and between
64-KiB image-read chunks. It discards cancelled snapshots and preserves normal
capture, identity checks, image-size limits, test selection and timing budgets.
Deterministic reader regressions were RED (one passed, two failed) before the
implementation and GREEN afterward. With the actual workflow build-id flags,
all 63 probe unit tests and Clippy with warnings treated as errors pass. The
fixed guard case passed 24/24 concurrent instances under the same 8-CPU quota,
each below one second. The fixed diagnostic includes build-id flags, so the
contention binaries differ in that respect; the reader regressions are the
direct RED-to-GREEN evidence. A blocked individual OS read remains
uninterruptible until it returns.

The real Linux quick run `c58ccee4-4503-42dd-b2f3-5dc420a0a2f4` is running on
the clean committed candidate. Its final result is pending. A separate shadow
attestation candidate preserves commands, matrices, triggers and dependencies
across seven existing PR workflows; it is reviewed but unpushed. An extracted
read helper removes a new 101-line unwinder complexity offender without an
exception. No attestation or migration success is claimed before full
validation. The earlier Hello latency failure also remains part of the pilot
record; the sampler fix does not prove that performance gate.

## Running-process rollout: October 4 UTC

The pending observations above are historical. The recovered and corrected
candidate completed the real gate, and
[running-process#1310](https://github.com/zackees/running-process/pull/1310)
merged as `809a3197706a1acf8952aaeab04f3a433bf34da0`. All seven PR workflows
passed on the first attested head; main CI
[37170075638](https://github.com/zackees/running-process/actions/runs/37170075638)
passed on attempt 1. This establishes one successful rollout head, not a new
fleet-wide 100% historical pass rate. The seven-workflow result also has a
different scope from the selected-workflow cohort table above.

Bosn run `40ae8167-c8a2-4bc3-b557-cbfbdc59d440` executed clean commit
`4bbdf210308c4b3e92e0f18f90af2e8802428061` and passed all four selected jobs
in 1,564.7 seconds. Ci-lint then stamped
`4cedbb2316aee2e65c9eae3b33440153557bce3d`, with the identical Git tree
`76accfaebee1368567346825de5647ed73d5567f`, and seven scoped attestations.
The stamped SHA was not the SHA executed by that run.

The actual gate passed 2,354 Rust cases (one retry and nine existing skips),
838 selected Python cases (26 skipped and 127 deselected), real lint, both
Dylint fixtures and workspace Dylint. Hello P50 was 118.875 microseconds and
P99 was 231.547 microseconds, inside the unchanged 200/1,000-microsecond
budgets. The pilot additionally reproduced a beacon handshake failure while
SQLite held a writer lock; serving the beacon before crash-store reconciliation
and giving daemon fixtures private stores resolved it without relaxing the
handshake or shutdown assertions. Source ratchets now run before engine
submission. Every PR entrypoint depends on the pinned verifier; trust remains
in shadow mode and remote coverage still executes.

The successful run saved 757.6 MB of compiler units with preflight target-tree
caching disabled. Rechecking its already attested head took 0.763 seconds and
started no engine. This measures unchanged-head proof reuse, not warm compiler
execution.

The forced warm run `3c0d9e5b-afb2-439f-9bab-e850c3cc181c` restored that exact
archive in 6.5 seconds (2.83 GB extracted, 2,943 files), but its initial
workspace build still reported 0 hits and 411 misses. Later stage hit/miss
counts matched the cold run, so they do not establish improved cross-run reuse.
The retained archive contains its index, metadata and dependency graph, but
its saved index lacks the representative initial-build
`pin_project_lite-c530514e9062bf77` outputs. The cause remains unknown; an
archive hit must not be reported as a compiler hit.

That warm attempt is failed infrastructure evidence. Act exited 137 after
952.7 seconds before Unit Tests completed; cleanup reported its engine was
already being removed, and the engine was absent on subsequent inspection.
Bosn returned failure with a cleanup failure and no new attestation. The
run's storage report peaked at 1,709.1/1,830.7 GiB used with 28.5 GiB free.
The measurement issued no engine removal, global prune/restart or cache
deletion. The removal actor and cause are unknown; evidence is recorded in
[bosn#456](https://github.com/zackees/bosn/issues/456#issuecomment-5975780726).

[running-process#1306](https://github.com/zackees/running-process/issues/1306)
still owns reliable cross-run compiler reuse and the staged Dylint migration:
existing bare tool installs, source driver builds, native Dylint lanes and
whole-target Dylint caching remain findings. The merged attestation rollout
does not claim full policy compliance or a completed warm-cache improvement.


### Running-process managed Dylint measurement (2026-10-03)

The next staged candidate replaces manual Dylint provisioning and persistent
fixture/workspace target snapshots with setup-soldr's managed prebuilt Dylint
6.0.3, nightly-2026-05-28, and reusable compiler units. Driver fallback is off;
target/output persistence is off. Both negative-fixture suites and both
workspace passes remain required. Failed jobs cannot publish, and remote
writers remain main-only. Native-lane and cross-target findings remain open;
this candidate does not establish RUST-002/003 compliance.

Two actual isolated runs of the existing reusable Dylint job completed:

| Run | Result | Engine summary | Receipt started-to-finished |
| --- | --- | --- | --- |
| `f4c107ce-ba5d-4474-9e9e-e7b6958e2d5b` | Cold success | 557.2 s | 557.564 s |
| `1e4ead30-fbf2-45b6-8a81-5748dee4aa43` | Fresh-engine warm success | 258.3 s | 265.337 s |

The warm run restored the exact foundation and 435.5 MB compiler archives.
Its env fixture reported 391 hits/0 misses, boundary fixture 36/0, and first
workspace pass 416/39. Repeated cumulative summaries are not added together.
The engine summary was 54% lower; the complete receipt interval was 52%
lower. Both real logs pass `ci-lint rust toolchain-build-check` with zero
findings. No whole target tree was retained.

These runs were dirty diagnostic snapshots on merge `809a319`. Their Rust
sources, dependency locks, commands and job environment match, while Python
helper/tests/comments differ. They demonstrate compiler reuse, not a
source-bound full-gate attestation of the final candidate. The normal
preflight compiler-reuse gap remains distinct and unresolved.

The compatibility constraints are explicit: `SOLDR_LINKER=default` respects
the lint crates' declared `cfg(all())` dylint-link while
[soldr#3483](https://github.com/zackees/soldr/issues/3483) remains open. Cargo
registry caching is disabled after an exact restore lacked
`dylint_internal 6.0.1/template.tar`; the workaround passes, but does not
implement the self-healing fix in
[setup-soldr#492](https://github.com/zackees/setup-soldr/issues/492).

The first full candidate run `80e3e6ae-380d-4f76-86e3-72c83008b173` failed
before publication: actual Dylint checks, lint, all 2,354 Rust tests and the
6/15-case feature passes succeeded, but Python reported 837 passed and one
failed. A stale workflow regression still expected the removed manual
Dylint cache paths. Its correction retains the Bash requirement and checks
the managed setup plus both pinned tool versions. Primary review is clean.
Corrected clean commit `4f235bb0e19f60ade79ca3935c464fe5ba19c776` passed the
complete gate in run `d886e1e4-cff9-4c42-b04d-128e73fdb523`: engine summary
913.0 s, complete receipt 913.210 s, outer attesting gate 917 s. All four
required jobs and their required steps passed. Rust reported 2,354 passed
(one flaky retry, one leaky test, nine existing skips), with 6/6 and 15/15
selected feature cases. Python reported 838 passed, 26 skipped, 127
deselected and 120 passing subtests in 53.80 s. Hello used 10,000 samples:
P50 111.26 µs and P99 282.534 µs, below unchanged 200/1,000 µs budgets.
Successful preflight compiler publication saved cache id 8; target caching
remained disabled. The complete log has zero RUST-009 findings.

The attesting wrapper stamped `c65968180acaedd296dcdaad7a3a1de6d8d61476`
with the identical Git tree `bb2850262f22fb187ad243edb87912e276c33d7b` and
seven scoped attestations. Source/check-push checks and primary review pass.
[PR #1311](https://github.com/zackees/running-process/pull/1311) is open;
first-head remote CI run 37174970554 is still in progress on attempt 1.
Remote rollout is not yet complete. Evidence and remaining findings are
tracked in [running-process#1306](https://github.com/zackees/running-process/issues/1306#issuecomment-5976181493).
