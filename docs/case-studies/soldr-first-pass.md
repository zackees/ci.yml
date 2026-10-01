# soldr: why PRs did not pass CI on the first push (2026-10-01)

Tracking issue: [zackees/ci.yml#166](https://github.com/zackees/ci.yml/issues/166).
Window: 2026-09-30 22:00 → 2026-10-01 19:00 UTC, `zackees/soldr`.

This is a case study, not policy. The rules it led to (`GATE-001..004`)
are written into [policy-general.md](../policy-general.md#local-gate-first-gate-001004).

## Baseline

`ci-lint local-gate first-pass --slug zackees/soldr --since 2026-09-30T22:00:00Z`
(merged PRs only; "first pass" = exactly one head SHA got a `ci.yml` run,
green on attempt 1, no failed run):

| PR | Result | Pushed heads | Notes |
| --- | --- | --- | --- |
| #3505 | miss | 3 | aarch64 target-run failure, fixed by a follow-up commit |
| #3506 | pass | 1 | |
| #3508 | miss | 3 | guard-test drift fixed by two follow-up commits |
| #3509 | pass | 1 | |
| #3511 | pass | 1 | |
| #3512 | miss | 2 | one fix-up push cancelled the first run |
| #3513 | miss | 4 | follow-up "test(ci): satisfy pylint in cost inventory guards" |

**3 of 7 = 43%.** Two more PRs were active but unmerged in the window:
#3510 (9 commits, 6 CI runs, still open) and #3514 (closed, one fix-up push).

## Failure classes

**1. Locally catchable, found remotely (the largest cost).**
- #3510: `Lint` failed on `.github/scripts/test_verify_workflow_paths.py::test_the_cache_crate_is_watched_where_it_now_lives`
  (run 36896402711, job 110484476466) -- a pure-Python test that takes seconds locally.
- #3513: pylint failure, fixed by a follow-up commit.
- #3508: "keep bootstrap version pins and wheel guards aligned", "declare
  zccache embedded by the published bootstrap" -- repository-consistency guard tests.
- #3512, #3514: one fix-up push each, cancelling the in-flight run.

**2. Native-only failures (real bugs a Linux x86 workstation cannot see).**
- #3505: `target-run aarch64-unknown-linux-gnu` failed with `exit status: 126`
  from the managed LLVM `clang` shim -- the exec bits were lost on `bin/`
  (run 36789756892, job 110149384005). Fixed by 375f88c; 3 pushes, ~1h45m.
- main, Autonomous Release 36801120603 (attempt 2 also failed): the
  x86_64-apple-darwin release gate failed
  `cargo_front_door_default_injects_reld_when_available` (job 110201285952);
  fixed by #3506.

**3. Shared infrastructure, not caused by the PR.**
- `CI Pre / Repository Actions cache budget` hard-failed with "family
  `setup-soldr-action-stores` uses 1.42 GiB, over its 1.30 GiB budget" on
  #3510's `workflow_dispatch` "CI full" run 36896402711 (job 110484379991)
  and on main CI 36896801976 and 36806205531 (main d5b12cd needed 3
  attempts), plus main Cache Budget 36877155349. Correction to the first
  read of this incident: soldr's `check_cache_budget.py` *already* reports
  pre-existing overage as a warning on `pull_request` (only the PR's own
  entries fail, matching `ci-lint cache budget`). The #3510 failure came
  from its exact-SHA "CI full" run, a `workflow_dispatch` on the feature
  branch, which `enforcement_mode` treated like main (hard fail) although
  that run is PR validation. The main-branch failures are policy working as
  designed (default-branch overage is a hard failure), but they turn main's
  CI red for a reason no code change caused.
- Flaky runner: #3510 `aarch64-pc-windows-msvc (1-of-3)` died with exit
  `-2147483644` (job 110496145351) and needed attempt 2. The macOS x86 leg
  depends on `/dev/kvm` and an image pull.

**4. Process waste.**
- Two `ci.yml` runs per pushed SHA, one cancelled (#3505, #3508, #3510):
  the `pull_request` trigger includes `labeled`/`unlabeled`, so labelling a
  new PR starts a second run that cancels the first.
- Cache Budget runs again after every PR CI.

## What was built

The class-1 failures all lived in soldr's `Lint` job, a ~40-step job whose
commands existed nowhere as a single local command. `ci_lint` now enforces
a *local gate first* contract (#166):

- `GATE-001`: a declared gate command (`[local.gate]` in `ci.toml`, or
  `local-gate.toml`) and the remote jobs that mirror it, which may run that
  command and nothing else -- remote ⊆ local by construction.
- `GATE-002`: the PR entry workflow verifies an attestation in its first
  job, and every other job needs that job.
- `GATE-003`: `ci-lint local-gate run` stamps the head commit with a
  tree-bound `Local-Gate:` trailer; `verify` (CI) and the pre-push hook
  refuse a head without one.
- `GATE-004`: `ci-lint local-gate first-pass`, the measurement above.

Class 2 stays remote-only by design; class 3 and 4 are soldr workflow fixes.

## Incident during the pilot: a host test run wedged the host's soldr

To size a Rust lane for soldr's local gate, `soldr ci-test` (the same frozen
DAG the remote `build-linux-x64` job runs) was run directly on the developer
host in a clean worktree of `origin/main` (2026-10-01 12:18:55-12:29:01
local, 606 s cold). Two things went wrong, and both changed the policy.

1. **The suite is not hermetic on a host.** 12 nextest tests failed that
   are green on CI runners (reentrancy guard, global-upgrade delegation,
   daemon generations, doc-route deadlines), because the host has a newer
   global soldr, live daemons of other generations, and agent PATH shims.
2. **The suite damaged the host.** A fixture
   (`direct_rustc_like_commands_route_through_zccache_with_and_without_global_flags`)
   started a broker-launched `soldr-daemon` (PID 253182) whose environment
   still carried the real `HOME`. It claimed the real `~/.soldr`
   root-ownership lock and was orphaned when the test exited. From then on
   *every* host `soldr cargo build` failed with "soldr root ownership is
   busy ... orphaned soldr-daemon", for every agent session on the machine.
   Killing that one orphan restored the host
   ([zackees/soldr#3516](https://github.com/zackees/soldr/issues/3516)).

Lesson, adopted as `GATE-005` ([#168](https://github.com/zackees/ci.yml/issues/168)):
for a repository whose tool is also live infrastructure on the developer's
machine, "run it locally first" must mean "run it in an isolated
container". The suite must refuse to start anywhere else (`CI=true` or an
isolation marker that only the bosn image sets), and the local gate runs it
through `bosn run --task test`. Soldr's bosn route had itself rotted
unnoticed: bosn now requires digest-pinned base images and
`docker/cook-shared-cache/Dockerfile` floated `rust:1.98.1-trixie` and
`ghcr.io/astral-sh/uv:0.9.18`.

## Pilot status

- soldr adoption PR: TBD
- post-adoption measurement: TBD
