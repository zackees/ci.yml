# Case study: template-python-rust-cmd — building the rust-pypi-app reference (issue #6)

Case studies record verified evidence from a specific repository incident or
investigation. They are not policy: a rule only binds once it is written into
[policy-general.md](../policy-general.md) or [policy-rust.md](../policy-rust.md).

This one is a round-by-round log of [zackees/ci.yml#6](https://github.com/zackees/ci.yml/issues/6),
which turned [`zackees/template-python-rust-cmd`](https://github.com/zackees/template-python-rust-cmd)
into the fleet's lean, machine-checked `rust-pypi-app` reference while `ci_lint`
was built beside it in this repository. It was compiled on 2026-09-29 from:

- issue #6's comments (`gh issue view 6 --repo zackees/ci.yml --comments`);
- merged PR bodies in both repositories (`gh pr list --state merged`), plus the
  PR comments that carry probe results (template #17, #26, #31);
- upstream issues setup-soldr#538/#540/#543, soldr#3433/#3444 and
  soldr-toolchain#191;
- two job logs that the PR bodies point at but do not quote
  (`gh run view <id> --log`), for the clud-scale forecast and the glibc floor.

Every number below cites its run, PR or log line. A claim that none of these
sources supports is labeled **unverified**. "PR #N" means a template PR and
"ci.yml#N" means a PR or issue in this repository, unless the text says otherwise.

## Starting point (template `main` @ `48c0884`, 2026-09-28)

This is from issue #6's evidence comment:

- **0 of 27 workflow runs had ever succeeded** (21 cancelled, 6 failed).
  A retired `macos-13` matrix entry waited 24 h and was cancelled on every run
  (28981005677, 28979172557, 28595285811).
- The coverage was partly fake. The two `musl` entries never passed `--target`.
  There were 0 Rust tests (`running 0 tests` ×4), and 2 Python tests skipped
  because the CLI was not on PATH.
- The workflow used bare `cargo`/`maturin` and `dtolnay/rust-toolchain@stable`,
  with no `setup-soldr`, no `timeout-minutes` and no SHA pins.
  `continue-on-error` was set on 8 of 9 gates.
- `actions/cache` saved `~/.cargo` + `target/`. An exact 71 MB hit still
  recompiled every dependency.
- Publishing was a disabled `twine upload`, with no OIDC.
- Baseline timings from push run 28981005677: linux-x86 80 s, linux-arm 106 s,
  windows-x86 140 s, windows-arm 170 s. PR run 28979172557 queued jobs for up
  to 15.5 min.

## Round 1: layout, packaging and the checker core

**Built.**
- ci.yml#10 added `ci_lint`: a strict schema-3 loader, a static precheck with
  34 rule IDs in 9 groups, and the tag planner. Every rule has a RED and a
  GREEN fixture; there were 65 tests.
- Template PR #16 built the layout:
  - a public amalgam crate `crates/template`;
  - private crates `template-core`, `template-json` and `template-platform`, with
    one `cfg_select!` selector, a cfg-free facade, and native code only under
    `platforms/{windows,linux,macos}`;
  - PyO3 0.29.2 abi3-py310;
  - the Soldr PEP 517 backend (`soldr==0.9.25`), with `bundle-bins` shipping the
    native `template-cli`.

  PR #16 also deleted the broken workflow, which left the repo with no CI.

**Measured (local, PR #16).**
- 18 Rust tests + 1 doctest, with zero skips. `pytest` ran 40 passed, 0 skipped.
- All 5 non-host targets cross-checked through `soldr cargo check`.
- The 5 test binaries totalled ≈ 34.8 MB against a 150 MB cap.

**Defects found.**
- The sdist could not build its own wheel. `rust-toolchain.toml` and
  `crates/template-cli/**` were missing from `[tool.maturin] include`. This was
  fixed, and the wheel is now also built from the sdist (PR #16).
- The first integration check found several linter false positives (issue #6
  "round 1 merged" comment):
  - it scanned the gitignored `.cargo/registry`, producing 7,127 bogus
    `LAYOUT-001` findings (ci.yml#11);
  - it matched selectors inside doc comments;
  - it rejected soldr's table form of `bundle-bins`;
  - `pre-prune` waived the whole `CACHE-004` budget instead of only the
    lockfile peak.
- Genuine template findings:
  - an unpinned `astral-sh/setup-uv@v5` (`SEC-004`);
  - `dylint-out` at 500 MB × 6 platforms forecast 14.3 GB against a 9 GB budget
    (`CACHE-004`), fixed with 150 MB per platform plus `pre-prune`.

**Decision.** `ci.sh` stays as a declared, expiring `[[exceptions]]` entry,
tracked in template issue #15.

## Round 2: workflows, runtime commands, the Dylint lane

**Built.**
- ci.yml#11 fixed the round-1 scan defects and added the runtime commands
  `units`, `tests size`, `wheel check|installed`, `gate`, `--local` and `selftest`.
- ci.yml#13 fixed parsing of Cargo package-ID specs that omit the name
  (`path+file:///…/template-core#0.1.0`). Template PR #17 found this defect.
- Template PR #17 added:
  - `ci.yml` + `ci-precheck.yml`;
  - the single `.github/actions/soldr` wrapper;
  - the `fast` lane;
  - one all-target Dylint job with a real `platform_boundary` lint;
  - the `CI OK` gate.
- Template PR #20 added bosn → act local lanes and a PostToolUse/Stop agent
  precheck hook.

**Measured.**
- Precheck took 5–8 s (target ≤ 30 s), per the issue #6 "rounds 2–3 merged"
  comment.
- The `fast` lane took 2m01 cold and 1m19 warm (same comment).
- Local precheck (PR #20) took 1.66 s cold and 0.39–0.46 s warm.
- A local `act --lanes fast` run took ~62 s cold and 4.0 s warm, but was blocked
  at checkout (see below).
- The GitHub cache count was 10 before and 10 after a local act run, so nothing
  local was uploaded.

**Defects found and fixed.**
- `cargo dylint --workspace` checked nothing: "Nothing to do. Did you forget
  `--all`?" appeared on 6/6 passes. Every earlier green Dylint run in PR #17
  (36481962865 through 36484456470) was a false green. It was fixed with
  `soldr dylint --all -- --workspace` (PR #17 comment) and later became static
  rule `RUST-002` (ci.yml#32).
- The Rust 1.95 bucket maps Dylint to `nightly-2026-02-28`, which has no
  catalogued `dylint-driver` 6.0.3. The from-source fallback fails with "soldr
  requires a repo-pinned toolchain" (runs 36486187381, 36487047790). The
  workaround is to pin `dylint-toolchain: nightly-2026-05-28`. This is filed as
  soldr-toolchain#191 and is still open.
- **Dylint caches never saved** (setup-soldr#538). The success-marker identity
  compared a short toolchain channel with a host-qualified one. On `main` push
  36494661966 and warm PR 36495222596, the logs say `dylint-cache: no matching
  successful Dylint marker - skipping save`. Evidence from PR #22:
  - RED runs 36496358860 and 36497556959;
  - GREEN runs 36497063367 and 36498249491;
  - exact-hit run 36498507523.

  The fix was setup-soldr#539, released as **v0.9.81** and ingested in PR #22.
- PR #20 recorded the act runner's missing `clang` (issue #6 D7) and fixed it
  with a clang-patched `catthehacker/ubuntu:act-24.04` image.
- The `CACHE-009` zccache#1760 scenario now fails **locally**, before act starts
  (PR #20).

**Decisions.**
- Clippy stays a separate required check, not folded into Dylint (PR #17).
- Wheels are built with `uv build --wheel`, not `soldr wheel --release`.
  `soldr wheel` measured faster locally (14.32 s vs 19.95 s), but it bypasses
  PEP 517, which `PKG-004` requires (PR #17).
- soldr's fail-closed bare-cargo trap script was not adopted. setup-soldr's
  `shims: true` is used instead (PR #17).

## Round 2E/2F: Dylint across commits (setup-soldr v0.9.81 → v0.9.82)

The Dylint lane went from 153 s to 125 s to 49 s:

| Run | State | Dylint job | Source |
|---|---|---|---|
| 36495222596 | pre-fix warm; both Dylint caches never saved | **153 s** (2m33; 6 passes 91.7 s) | setup-soldr#540, PR #22/#23 |
| 36501659725 | v0.9.81, new commit: `dylint-cache` HIT, `dylint-output-cache` **MISS** (source-SHA key) | **125 s** | setup-soldr#540 |
| 36501247176 | v0.9.81, same-commit rerun, both HIT | 118 s | setup-soldr#540 |
| 36504380511 | v0.9.82 candidate, cold, new `v2` key: MISS → SAVED 71.4 MB | 1m24s | PR #25 |
| 36504625315 | 1 Rust line edited, lockfile unchanged: **EXACT HIT**, `template-core` still rechecked | 54 s | PR #25 |
| 36505737115 | `#[cfg(windows)]` outside the facade: EXACT HIT, and Dylint **fails** on the new violation | failure (required) | PR #25 |
| 36507439579 (job 109211987053) | docs-only PR on a new SHA: EXACT HIT on the key `main` saved in run 36507044108 | **49 s** | PR #26 comment |

- After the v0.9.81 merge to `main`, run 36501247176 saved `dylint-cache` as
  668,506,333 B and `dylint-output-cache` as 74,568,687 B (PR #23).
- The root cause of the remaining MISS: setup-soldr's `dylintOutputHash`
  included `source_revision: githubSha` (setup-soldr#540). The fix was
  setup-soldr#541, released as **v0.9.82** and ingested in PR #25. It keys on the
  real inputs, with a `restore-keys` fallback that drops only `Cargo.lock`.
- **Resolved (round 7B):** the 153 s figure is the template's own pre-fix
  warm Dylint run 36495222596, as setup-soldr#540 and PR #22 state. The PR #26
  comment's "clud's host-only Dylint lane" label is wrong; clud's host-only
  lane measured **235 s** (run 36418266760, issue #6 Dylint comment).
- The multi-target shape (D6) beat sequential by 23–37 %. On one commit, both
  cold: 55.5 s vs 88.8 s of check time, and 114 s vs 148 s job total (PR #19).
  `[lint.dylint].shape = "multi-target"` was adopted.
- The `v0` floating tag was **not** moved. That needs a FastLED/fbuild canary.
  The template pins the exact SHA (PR #22, #25).

## Round 3: tag-selected platform lanes and title-edit reuse

**Built.**
- ci.yml#15 added planner platform-lane outputs, per-lane digests, `--reuse`
  and gate reuse verification.
- ci.yml#21 read `GITHUB_EVENT_PATH` when `--event` was omitted.
- Template PR #19 cross-builds on Linux through Soldr, then executes on native
  Windows/macOS/ARM runners with no Rust toolchain.

**Measured (PR #19).**

| Scenario | Run(s) | Result |
|---|---|---|
| untagged push | 36497581255, 36502834054 | green; critical path ~105–207 s |
| `[ci-windows-arm64]` | 36500282280 | green, single lane |
| `[ci-full]` | 36501739705, 36502221614 | green on 5 platforms + integration; critical path ~226–257 s |
| `[no-test]` | 36500675385 | gate red, as expected: "tag(s) no-test remove required coverage" |
| `[ci-foo]` | 36500931481 | precheck fails `TAG-001` in 7 s, as expected |

**Defects found and fixed.**
- Windows `run:` defaulted to pwsh, so `"$VAR"` was empty (36498352193).
- Artifacts lose the executable bit (36498870719).
- `uv` picked an emulated x86 Python on `windows-11-arm`.
- `env!("CARGO_BIN_EXE_*")` broke `cargo check --all-targets`.
- **Title-edit reuse never fired: 0 of 4 attempts.** The plan step passed
  `--reuse` without `--event`, so no head SHA was found. The gate failed safe;
  it did not pass falsely. Fixed in ci.yml#21, and reuse was re-verified in
  PR #24 ("gate prints `reused from run N`").
- Round 3 needed two `SEC-002` `[[exceptions]]` for `actions: read`. They were
  removed once ci.yml#22 added per-job `[allow].permissions`.

## Round 4: declared cache families, writer-only saves, PR deltas

**Built.**
- ci.yml#20 added the cache runtime:
  - `save-ok`, the §6 do-not-save table (`CACHE-008`);
  - a live audit;
  - `trim`/`janitor`/`heal`/`preprune`;
  - `delta manifest|pack|apply`.
- ci.yml#22 added the `[allow].permissions`, `cache-actions` and
  `setup-uv.require = "plan"` refinements, plus static `GEN-004`.
- Template PR #24 added:
  - cache families declared at measured live sizes. `dylint` (~668 MB) was
    previously undeclared, so every run hit `CACHE-001`;
  - `per=` corrected from source-verified key shapes;
  - `save-cache` bound to the plan;
  - the `cache-maint` job;
  - Ruff + Pylint in `fast`;
  - act parity (`act --lanes fast` now passes end to end locally).
- Template PR #27 added the no-cache `init` job (`ci/instantiate.py`) and
  ci.yml#26 made the gate require it.
- Template PR #31 added:
  - the sanctioned `.github/actions/cache` wrapper;
  - a PR compile-delta with `-g<sha8>` generations;
  - `cache trim` in precheck.

**Measured.**
- Writer-only saves (PR #24): across 6 real pushes, zero new cache entries on
  `refs/pull/24/merge` after the fix.
- PR delta probe (PR #31 comment):
  - push 1, run 36513531709: a clean miss; packed 76 files, 1,215,988 B, and
    saved;
  - push 2, run 36514250621, a new commit: **HIT by prefix**; 87 files,
    1,223,235 B re-saved; the previous generation was deleted.

  Exactly one `delta-v1-pr31-*` entry survived. This settles issue #6 §6's open
  question: a new push, not only a re-run, restores a PR-scoped entry.
- D3 recompile-scope probe (PR #29), from PR #33's baseline paragraph:
  - `fast` took 75 s. The edited crate and its dependents recompiled.
  - `dylint` took 49 s, with hits=2, misses=24 (7.7 % hit rate).
- `preserve-source-mtimes` (PR #33 → PR #34):
  - It was correct: the same recompile scope as PR #29.
  - It was not faster: `fast` 82 s vs 75 s, `dylint` 51 s vs 49 s.
  - It was reverted to `false`, because the keep criterion is "correct AND
    faster".
- **Clud-scale simulation** (never-merged branch `sim/clud-scale`, a ballast
  crate with 180 locked packages; run 36514690030, `cache-maint` job,
  `ci_lint cache preprune --lockfile-changed`):
  - forecast = **7,389,425,296 B**: steady 5,153,281,864 B + lockfile peak
    1,162,401,608 B + PR budget 1,073,741,824 B;
  - the declared `[cache].budget = "9GB"` is 9,663,676,416 B;
  - so the forecast is ~7.4 GB (6.9 GiB) of a 9 GiB budget. Taken from the job
    log, not a PR body.
  - The same run's precheck static proof gave a worst case of 6,021,971,968 B.

**Defects found.**
- The hyphen-blind delta-key regex mis-split family/platform IDs (ci.yml#20,
  fixed).
- `platform-run`'s bare `if:` got an implicit `success()`, which skipped it
  whenever `cache-maint` was skipped (PR #24, fixed).
- The classifier did not recognize `-g<sha8>` delta keys. As a result,
  `cache trim` deleted 0 of 3 closed-PR deltas. After ci.yml#34 and PR #35 it
  deleted all 3 in one call.
- **setup-soldr#543 (open):** the build-cache "tiny-delta-skip" logged
  `0 new compile(s)` and skipped the save after ~130 fresh crates on the
  clud-scale push (run 36514690030, `fast`).
- **`preserve-source-mtimes` re-probe, leaf-crate edit (ci.yml#41, round
  M2-16, PR #47):** #41 asked for a probe shaped to actually exercise mtime
  replay, since PR #29's D3 probe edited the workspace's root crate
  (`template-core`), which forces the whole downstream chain to recompile
  regardless of the flag. PR #47 instead edits only
  `crates/template-cli/src/main.rs` (a doc comment) -- `template-cli` is a
  true leaf, nothing else in the workspace depends on it -- with
  `preserve-source-mtimes: "true"` on both the `fast` and `dylint` jobs, then
  the same commit was re-run to test replay against a saved snapshot.
  - Run 36519575251 (first push), `fast` job 109249344735: `cook-cache-base:
    no entry for key ...` (MISS, first run in this PR); `source-mtime-replay:
    snapshot file not found ..., skipping`. All 5 workspace crates compiled
    (`template-platform`, `template-core`, `template-json`, `template`,
    `template-cli`), as expected on a cold cache. `fast` took 1m30s, `dylint`
    59s, `CI OK` green. At job end: `source-mtime-snapshot: wrote
    .../setup-soldr-source-mtimes.json scanned=34 hashed=34 skipped=0`.
  - Same run re-triggered (`gh run rerun 36519575251`) same commit, `fast`
    job 109381182606: `cook-cache-base: no entry for key
    cook-base-v2-linux-x64-glibc-rustc1.95.0-fnone-l40d011b8bf414a69-soldrv0.9.26`
    (MISS again) and `0/5 exact-hit 1/5 any-hit` (only the cargo-registry
    layer hit). All 5 workspace crates compiled again, plus `template-py`
    which had not shown as a separate "Compiling" line in the first run's
    grep window. No `source-mtime-replay` log line appears at all in the
    rerun (not even a "skipping" line) -- the replay path was not exercised.
  - **Root cause:** this repo's `[cache]` policy in `ci.toml`/`ci.yml` only
    saves a `cook-base-*` build-cache layer on the default branch; PR runs
    use `cook-delta` only, and this workflow's PR jobs additionally run with
    `cook-delta=false` set by the delta-cache gate (`ci/cache_delta.py`),
    so a PR run -- including a same-commit rerun of a PR run -- never has a
    saved base layer to restore, and therefore never has a source-mtime
    snapshot artifact tied to a *restored* base layer to replay onto. The
    flag's mtime-preservation mechanism is scoped to the `cook-base`
    restore path, which this probe shape cannot reach from a PR context.
  - **Conclusion:** the probe is evidence, but it is inconclusive for the
    question #41 actually asked (does mtime replay limit recompilation to
    the edited leaf crate). It does confirm `preserve-source-mtimes: true`
    remains *correct* here too (both runs green, no `CI OK` regression,
    same test/lint results as `false`), consistent with round 4C's "correct,
    not faster" finding. A conclusive replay test needs a base-cache-saving
    context: two pushes to a real default branch (or a branch configured to
    save `cook-base`), the second push touching only a leaf crate, comparing
    the second push's compiled-crate list against the first. That is out of
    scope for a template PR probe and is left as a follow-up if the flag is
    ever proposed for `true` on `main`. Given the flag has never measured a
    win (4C: correct-not-faster; M2-16: cache layer unreachable from PR
    context), the template's shipped default stays `preserve-source-mtimes:
    "false"` (PR #34); PR #47 is evidence-only and was not merged.

**Owner decisions recorded on issue #6 (2026-09-28).** The first implementation
of these is in zackees/soldr; the template is not covered here.
- The PR-context live cache check is warn-only for state the PR did not create.
- The janitor runs on every push, schedule and dispatch, with one repo-wide
  concurrency group. It is a hard failure only on `main` or a schedule.
- PR-branch entries are deleted only for PRs that are closed or merged, both by
  the janitor and by a `pull_request: closed` job.
- Every PR-context cache key embeds `pr-<N>`. A proposed `CACHE-*` lint would
  flag a PR save without it.

## Round 5: release dry-run, OIDC mock publish, settings

**Built.**
- ci.yml#24 added:
  - `ci_lint audit` (`SEC-005/006/007`, `GEN-006`, `GEN-011`);
  - `publish oidc-check`;
  - `release verify` (`PKG-006`);
  - `perf compare`.
- Template PR #30 added:
  - the exact-SHA `release-guard`;
  - release-profile wheels for every platform, with linux-x64 built through
    Soldr's manylinux_2_17 cross sysroot;
  - `ci/release.py glibc-check`;
  - `release-verify`;
  - a `publish` job directly in `ci.yml` (`environment: pypi`,
    `id-token: write`);
  - a non-gating `perf` job;
  - a `pypi` environment restricted to `main`;
  - branch protection requiring `CI OK`.

**Measured.**
- `[release][ci-perf]` PR rehearsal, run 36513404482: all green, and
  `release-verify` reported 0 violations.
- Post-merge `workflow_dispatch`, run 36513922291. These job log lines were
  checked for this study:
  - `release_guard: PASS -- 6dea552… is HEAD and reachable from origin/main`
  - `release-linux-x64`: `glibc-check --max-glibc 2.17` →
    `max required GLIBC_2.16`, so the release wheel requires **glibc 2.16 ≤ 2.17**
  - `publish`: `ci-lint publish oidc-check: PASS` and `mock publish: stopping
    before upload`. PR #30 reports zero `eyJ` hits in the job log.
- A dispatch with a SHA not reachable from `main` fails at `release-guard`
  (36511708290 before merge, 36513729378 after).
- `ci-lint audit` reported 0 findings for `SEC-005/006/007`, `GEN-006` and
  `GEN-011` after the settings changes (PR #30).

**Defects found.**
- `PKG-006` did not match compound tags such as
  `manylinux_2_17_aarch64.manylinux2014_aarch64` (ci.yml#29, fixed).
- **soldr#3444 (open):** `bundle-bins` fails on the sdist-then-wheel build when
  the bundled bin is not a Cargo dependency of the extension crate. The
  workaround is two separate `uv build --sdist` / `--wheel` calls. This gives up
  only the "wheel built from this exact sdist" property.

## Round 6: policy adoption and checker polish

- ci.yml#25 was docs only. It adopted the enforced rules into the policy docs,
  renumbered the clud `GEN-005` candidate to `GEN-010`, marked `proposal.md`'s
  contract as superseded, and added the rule-ID registry.
- ci.yml#27: Rust scans now see attributes and macros that are split across
  lines. The trigger was a multi-line `#[cfg(\n windows\n)]` that only Dylint
  caught.
- ci.yml#28 added:
  - `RUN-001` resolution for the platform-lanes matrix;
  - `suite check` (`TEST-001`/`TEST-002`);
  - `example drift`. Its first run against the template found 12 policy drifts,
    resolved in round 7A (below).
- ci.yml#32 added the Dylint rules. Each was checked against the real template:
  - `RUST-002` (static), 0 findings;
  - `RUST-003` (`dylint coverage`, runtime), all 6 platforms covered;
  - `RUST-004` (live), 0 findings.

  `RUST-004` was narrowed after the stricter "created after the qualifying job"
  signal false-positived on a healthy exact hit.

## Round 7A: example reconciliation

`ci_lint example drift` against template `main` @ `a7e58c4` found 12 policy
drifts. Every one reflected measured learning on the template side, so the
canonical example adopted the template's values. The classifier needed no
change.

**Adopted in the example:**
- `[lint.dylint]`: `shape = "multi-target"` and `budget = { warm = "115s",
  cold = "235s" }`.
- `per = "none"` for `registry`, `deps`, `dylint`, `dylint-out` and
  `soldr-mini`, and `per = "platform"` for `uv`.
- `actions/cache` in `[allow].actions`.
- `fast` added to the `actions: write` grant.
- The explicit `cache-actions` table was dropped from the example. It equals
  the schema default (`.github/actions/cache`), and the template omits it.

The result is 0 policy drifts and 11 repo-specific differences: sizes, the
`linter` pin, and suite `run` commands.

## Round 7B: remaining scenario evidence

Every run below was checked with `gh run view` / `gh run view --log`.

| Scenario | Run | Evidence (log lines) |
|---|---|---|
| D1 warm `main` push | 36509045219 | `fast`: exact HIT on `buildcache`, `cargoregistry`, `cook-base`, `soldr-mini`; `dylint`: exact HIT on all 4 layers; both jobs `layers_saved=0/2 uploaded=0B`. Only setup-uv saved: `Sent 43786800 of 43786800` (43.8 MB). |
| D3 one-line `template-core` edit | 36509424818 (PR #29) | restore layers all HIT (build-cache 18.3 MB, registry 5.8 MB, cook-base 25.7 MB, soldr-mini); the chain `template-core` → `template-json` → `template` → `template-cli` recompiled (`0 HIT, 4 MISS`); `fast` 1m15s, `dylint` 49 s. |
| D4 `Cargo.lock`-only libc bump | 36509863041 | cache-maint: `lockfile-changed: True`; preprune forecast = steady 4,948,230,144 + lockfile peak 957,349,888 + PR budget 1,073,741,824 = **6,979,321,856 B** < budget **9,663,676,416 B**, `under budget: nothing to preprune`, `deleted 0`. 5 new entries: dylint build-cache (80.0 MB) + cargo-registry (27.8 MB) + dylint-output (id 8242135235); fast cargo-registry (5.8 MB) + cook-cache-base (27.3 MB). |
| D5 poison and heal | local `ci-lint cache heal` | `setup-soldr-cargoregistry-v1-linux-x64-a87d2454829c34f1-b72e0ca860d92638` (id 8237268308, `CACHE-006`) deleted, 22 → 21 entries. Verified afterwards: id 8237268308 is gone; the key was re-saved as id 8242093340 (2026-09-29T01:51:20Z) by a later run. |
| init job | 36509118845 (PR #27) | `init` job 109217234549 succeeded in 1m32s. |
| platform-run fix | 36508656115 (PR #24) | `platform-build` + `platform-run` succeeded for `windows-x64` (1m38s / 30 s) and `windows-arm64` (2m0s / 58 s); artifacts `platform-windows-x64`, `platform-windows-arm64`. |

## Scenario status (issue #6 Dylint comment, D1–D8)

| # | Status | Evidence |
|---|---|---|
| D1 warm `main` saves nothing redundant | done | 36509045219 (only setup-uv saved); cold Dylint save 36501247176 |
| D2 warm docs-only PR | done | 36507439579: exact hit, 49 s |
| D3 one private-crate edit | done | 36509424818; PR #33/#34 mtime replay; run 36504625315 |
| D4 lockfile change | done | 36509863041 |
| D5 poison and heal | done (local heal) | id 8237268308 deleted |
| D6 invocation shape | done: multi-target | PR #19 |
| D7 local act | done for `fast` | PR #20, PR #24 |
| D8 dylint-driver through zccache | not reported in these sources | unknown |

## Open items

- **Upstream issues:**
  - setup-soldr#543: the tiny-delta-skip skips a large, genuinely new compile.
  - soldr#3433: the `WHEEL` Generator says maturin, so `PKG-004` cannot prove a
    Soldr build from bytes.
  - soldr#3444: `bundle-bins` fails on the from-sdist build.
  - soldr-toolchain#191: the 1.95 bucket has no Dylint driver.
- setup-soldr `v0` has not been moved to v0.9.81/v0.9.82.
- D8 evidence is missing from the published sources (table above).
- `ci.sh` is still an approved exception (template issue #15).
- The owner's janitor, PR-key and `pr-<N>` decisions still need to reach
  `ci_lint` and the template.
