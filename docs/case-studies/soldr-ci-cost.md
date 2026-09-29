# Case study: soldr CI cost — disk exhaustion, fan-out, and gate concurrency

Case studies record verified evidence from a specific repository incident or
investigation. They are not policy: a rule only binds once it is written into
[policy-general.md](../policy-general.md) or [policy-rust.md](../policy-rust.md).
This one covers `zackees/soldr`'s own CI, in two waves: the test-target
fan-out incident that broke a runner outright (2026-08-27/28), and a second
wave of gate-concurrency fixes a week later (2026-09-06/07) that cut real
wall-clock on top of the fan-out fix. Every number below is sourced from a
`gh` command against the public repository; where a before/after pair could
not be directly measured, that gap is stated rather than estimated.

## Wave 1 — the incident

`crates/soldr-cli/tests/` had grown to 98 top-level files (+3 in
`soldr-daemon`, +1 in `soldr-cache`) = ~110 linked test binaries. Cargo links
the full soldr dependency graph into each one.

**The failure** (run `33065541438`, PR CI on commit `dcf145c4`,
2026-08-27T11:01:19Z, `event: pull_request`, `conclusion: failure`):

- `target-run x86_64-pc-windows-gnu (1-of-3)` died on raw `os error 112`
  after 1060s. `cargo-nextest` decompressed a **3,302,138,143-byte**
  (3.3 GB) archive into `C:\...\AppData\Local\Temp` — nothing told it where
  to put it — while `C:` started at 31.03 GiB free and `D:` sat at
  143.61 GiB free, untouched, for the whole run.
- The archive was inflated **twice per shard** (`nextest list` and
  `nextest run` each re-extract `--archive-file`) across 3 shards — ~6
  decompressions of 3.3 GB paid by one PR for this one target.
- A prior DWARF-strip mitigation (#2883/#2886) was already active in this
  run and recovered only **~5%** (3.49 GB → 3.30 GB) — the dominant bytes
  were duplicated static linking, not debug info.
- The other two windows-gnu shards succeeded at **1493s** and **1503s**
  each — that is the "before" per-shard cost once the archive is merely
  large rather than fatal.
- The same run's `Linux x64 / x86_64-unknown-linux-gnu` job (build + lint +
  unit + integration in one job) ran **2938s** (48m58s).

Root cause and fix tracked as soldr#2931 (meta), six phases, all closed:

| Phase | Issue | What |
|---|---|---|
| 1 Containment | #2933 | Extract once per shard, onto a probed roomy volume (`select_extract_volume.py`), not the OS temp volume; hard disk guard names the path/bytes on failure instead of surfacing as an unrelated test failure |
| 2 Consolidation | #2934 | 98 top-level files → **8 category targets** (`broker`, `daemon`, `cargo_front_door`, `cache_gc`, `cook_dylint`, `fetch_tools`, `toolchain_env`, `guards`); 110 → 12 workspace-wide link products |
| 3 Store hygiene | #2935 | Bump the pinned zccache dependency to 1.13.14, adopting zccache#1525's `--test`-product exclusion (PR #2969) |
| 4 Warning | #2936 | `soldr ci-test` counts real link targets and prints `WARNING: LOTS AND LOTS OF TESTS DETECTED (>50)` above a threshold (`SOLDR_TEST_TARGET_WARN_COUNT`, default 50, warn not fail) |
| 5 Policy + guards | #2937 | `ci/cache-ownership.json` manifest + `Lint`-job guard (`check_cache_ownership.py`) refusing linked test/bench/example/doctest products in a reusable store; retires the #1391 invariant that had required the opposite |
| 6 Docs | #2938 | Fix agent docs that had mandated caching linked test products |

Phases 1–5 landed together in PR #2940 (merged 2026-08-27T22:45:22Z, +5038/
-354 across the workspace); phase 6 landed slightly earlier in PR #2939.
PR #2940 records parity directly: **489 `#[test]` functions before, 489
after** — nothing dropped — verified by `cargo test -p soldr-cli --no-run`
linking cleanly and every category `main.rs` module list checked against its
directory.

### Measured: before vs. after the fan-out fix

Comparing the incident run (`33065541438`, 2026-08-27, pre-fix) against the
next available full-tier run that exercises the same jobs
(`36363147463`, `workflow_dispatch`, 2026-09-28, post-fix — see caveat
below):

| Job | Before (Aug 27) | After (Sep 28) | Change |
|---|---:|---:|---:|
| `target-run x86_64-pc-windows-gnu` (per shard, 3 shards) | 1493–1503s (1 shard failed at 1060s) | 219–273s | **~5.8x faster** |
| `Linux x64 / x86_64-unknown-linux-gnu` (build+lint+unit+integration) | 2938s | 723s | **~4.1x faster** |
| `cross-build x86_64-pc-windows-gnu (linux host)` | 1133s | 747s | ~1.5x faster |

**Caveat:** the "after" run is 32 days later than "before" and reflects both
Wave 1 (fan-out consolidation, landed Aug 27–28) and Wave 2 (gate concurrency
fixes below, landed Sep 6–7) together. No run in the available history
window falls cleanly between the two waves, so their individual
contributions to the `Linux x64` number are not separable from run evidence
alone — Wave 2's PRs report their own component-level measurements
independently (below), which is the best available isolation. The
`target-run windows-gnu` improvement is attributable to Wave 1 specifically,
since Wave 2's changes target the Linux gate only.

Archive-size "after" was not independently re-measured in this
investigation (would require reading a recent run's
`nextest_archive_bytes.py` step summary); the reported **110 → 12** link
products (91% fewer) from PR #2940 is the direct evidence for the byte
reduction, not a separately confirmed compressed-archive size.

## Wave 2 — gate concurrency (2026-09-06/07)

A second, independent round of fixes cut the Linux gate's wall-clock further
by finding cost that had nothing to do with link count.

**PR #3156** — `perf(gate): drop an undocumented whole-runner reservation, unfloor the test wrapper` (merged 2026-09-07T04:41:45Z):
- `nextest_timeout_wrapper.py` polled with `time.sleep(0.05)`, putting a
  ~50ms floor under every test on Unix. Measured directly in a gate run:
  **2,023 timed tests, fastest landed at 0.066s, none under 0.06s** — in a
  suite where ~1,700 tests are sub-0.2s unit tests. Replaced with
  `Popen.wait(timeout=...)`, which returns the instant the child exits.
- An undocumented `threads-required = "num-cpus"` reservation on one test
  measured costing **25.1s of whole-runner idle** (3 of 4 threads parked,
  t+88s..t+113s of a 1046s stage) — removed.
- A longest-job-last reordering was *tried and rejected*: soldr#3024
  attempt 2 had already tested it and Fresh Nextest **regressed from an
  18m11 median to 26m14** — recorded in `.config/nextest.toml` so it isn't
  retried blind.

**PR #3161** — `perf(gate): run the soldr-runtime group two-wide on Linux` (merged 2026-09-07T06:24:47Z):
- The `soldr-runtime` nextest test group ran `max-threads = 1`: 68 tests
  summing to **961s of strictly serial work inside a 1046s stage — 92% of
  the stage was one test at a time** on a 4-thread runner.
- Raised to 2-wide, gated to Linux only (every non-Linux lane keeps
  `max-threads = 1`) after checking all 15 documented soldr incidents in
  that group's history and confirming **none was memory-related** — the
  group exists to protect a 120s route-acquire deadline, not RAM.
- Measured contention: isolated 63–78s per test reaching 121s when racing
  (~1.7x), so 2x concurrency was calculated as a net **~1.18x throughput
  win** — deliberately not raised further.

**PR #3154** — `fix(admission): measure heavy Rust test links instead of naming them` (merged 2026-09-07T04:49:32Z) — a correctness fix with a Wave-1 tie-in worth recording as its own pattern: `SOLDR_HEAVY_TEST_LINKS` named `soldr_cli`/`soldr_daemon` for exclusive build-system admission, but after #2934's consolidation the workspace's heaviest links became the 8 category binaries (`broker`, `cache_gc`, …) — **none of which was in the name list**, so exactly the binaries that produced the original 3.3 GB archive had lost their exclusive-admission protection. Fixed by measuring summed `--extern` rlib bytes (threshold calibrated from a 6,876-record compile journal: **147–231 MB** for a full-graph `--test` link vs a few MB for a trivial one) instead of naming targets by name.

## Candidate policy rules

`RUST-005` and `RUST-006`, proposed in
[clud-ci-cost.md](clud-ci-cost.md), directly generalize this case study's
Wave 1 (link-target fan-out and linked-test-product cache admission); both
are now written into [policy-rust.md](../policy-rust.md) (round 6A,
zackees/ci.yml#6) — `RUST-005` enforced today in a narrower declared-budget
form, `RUST-006` declared but not yet fully enforced (pending candidate
`CACHE-007`). One additional pattern from Wave 2 is not yet covered by an
existing candidate:

| Candidate ID | Mechanical signal | Status |
| --- | --- | --- |
| `RUST-007` | An admission list, exception table, or allowlist that grants a build/test target special handling (exclusive resource access, a cache exemption, a timeout override) is keyed on a **name** rather than a **measured property** (size, link weight, duration). A rename or refactor — such as consolidating test targets — silently drops matching members back to default (frequently unsafe) handling with no error. | Candidate — not yet implemented; see policy-rust.md |

## Sources

- soldr: issues #2931, #2933, #2934, #2935, #2936, #2937, #2938; PRs #2939,
  #2940, #2969, #3151, #3154, #3156, #3161; runs `33065541438`,
  `36363147463`.

All issue/PR/run references were fetched live via `gh api` / `gh issue view`
/ `gh pr view` / `gh run view` against the public repository; none are
inferred from memory.
