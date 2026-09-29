# Agent operating guide

Use this guide when investigating or implementing fleet CI checks. The goal is to spend agent context on ambiguous cases after mechanical evidence has already been collected. This is a specification for planned automation, not a description of an existing scanner.

## Mechanical pass before agent diagnosis

1. Inventory active repositories under `zackees`, `FastLED`, and `TechWatchProject`; record default branch, language/artifact profile, workflow files, declared required checks, and documented exceptions. Keep the future inventory in one versioned central source of truth.
2. Parse current workflow YAML and follow local or accessible reusable workflow calls. Check the `pull_request` entry point, required check names, effective runner labels, Soldr/setup-soldr use, Dylint invocations, Python lint checks, and package smoke tests. A command hidden behind a script or unresolved expression is **needs review** until inspected.
   For native artifacts, record the execution host and each artifact target separately: Linux musl versus glibc 2.17, Windows GNU versus MSVC, and x64 versus ARM64. Do not infer libc or ABI from the runner name. Validate the artifact and its runtime compatibility where evidence is available.
3. Query Actions `pull_request` runs and their jobs and steps. Calculate the required PR critical path and job execution separately from queue time. Collect representative run, job, workflow, and commit links. Treat `pull_request_target` runs separately because their permissions and trigger semantics can differ.
4. Compare current configuration with historical runs. Mark fixed cases as historical; never infer that deleting a slow workflow preserved test coverage. Collect explicit cache restore/save and toolchain identity evidence for Dylint cache findings.
5. Apply the [general](policy-general.md) and [Rust](policy-rust.md) rules. Emit a machine-readable result for each rule: status, repository, rule ID, subject, evidence URLs, observed values, and applicable exception. Send only violations and unresolved cases to agent review.

GitHub documents the [workflow run](https://docs.github.com/en/rest/actions/workflow-runs) and [workflow job](https://docs.github.com/en/rest/actions/workflow-jobs) APIs needed for timing collection. Reusable workflows can hide jobs behind a caller, so inspect their definitions as well as the caller; see [GitHub's reuse reference](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations).

## Findings and issue lifecycle

Use the stable `GEN-*`, `PY-*`, and `PERF-001` IDs in the [general policy](policy-general.md) and `RUST-*` and `PKG-*` IDs in the [Rust policy](policy-rust.md). Keep these IDs stable when implementing the result schema; change a rule's version rather than reusing an ID for different behavior.

For a confirmed finding, fingerprint `owner/repo + rule ID + workflow/job identity`. Search for an open issue with that fingerprint. Create one issue if absent; otherwise add new measurements and current workflow evidence to it. Include the violated rule, current file/line link, representative run and job links with dates and durations, whether cache state is known, affected coverage, and one concrete remedy. Do not file issues from a single ambiguous run or a text-only match to a script name.

The disposition is either:

- **Repository correction:** change the workflow, verify the required checks still execute and pass, and compare before/after timing on comparable runs.
- **Policy correction or exception:** document why the fleet rule is unsuitable, specify compensating coverage, name an owner and review date, and update the central policy or exception record before closing the finding.

For implementation changes to the checker, use focused regression fixtures from [issue #1](https://github.com/zackees/ci.yml/issues/1) and [issue #2](https://github.com/zackees/ci.yml/issues/2): repeated native Dylint, qualified versus short nightly cache identity, read-only restored output, missing cross-target `rust-std`, queue-heavy runs, a historical fixed case, a removed test workflow, equivalent Soldr command forms, and an approved dependency-cycle exception. Require a failing fixture before a fix and a passing fixture afterward.

## Current limits

The fleet inventory in issue #2 sampled repositories and PR history; it is not proof that uninspected repositories comply. The first central-scanner slice exists ([issue #38](https://github.com/zackees/ci.yml/issues/38)): `python3 -m ci_lint fleet scan` inventories repositories live and read-only (ci.toml adoption `FLEET-001`, PR entrypoints `GEN-001`/`GEN-008`, runner labels `RUN-001`, setup-soldr pin freshness `RUST-014`, settings `SEC-007`/`GEN-006`, cache usage `FLEET-002`) and ranks them, and `python3 -m ci_lint sync-issues --dry-run` prints the exact fingerprinted issue plan (one issue per repo + rule + subject, per the lifecycle above). No scheduler exists, and still no repository is enrolled: an agent still reports the observed scope rather than claiming fleet-wide enforcement for anything the scan does not check (Dylint, cache families, timing).

Round M2-42 ([issue #98](https://github.com/zackees/ci.yml/issues/98)) added a write path, `python3 -m ci_lint sync-issues --apply`, gated by three independent guards a reader can verify without touching the network:

1. **Per-repository opt-in.** A repository must set `[fleet].sync-issues = true` in its own `ci.toml` (schema 3, `ci_lint.schema.FleetConfig`) before `--apply` will ever create, update, or close an issue there. `--dry-run` still prints the plan for every scanned repository regardless of this key, so an owner can see what enrolling would do before opting in. `--apply` against a repository that has findings but no opt-in reports it under "repos with findings but no opt-in" and performs zero API calls for it -- not even a GET.
2. **`--max-writes N`** (default 5) caps the number of issue writes (create + update + close, each counted once) performed in a single `--apply` run. Actions beyond the cap are reported as skipped, not silently dropped -- rerun to pick up the remainder.
3. **The fingerprint marker.** `fetch_existing` only ever recognizes an existing issue whose body contains `<!-- ci-lint-fingerprint: <16 hex> -->`; `update`/`close` act exclusively on issues found that way, and the write path re-checks the marker is present in the body it is about to send as a second, structural guarantee. `sync-issues` never touches an issue a human filed by hand with the `ci-lint` label but no marker.

`--apply` is idempotent by construction: `plan_repo`'s title/body comparison against the fetched `ExistingIssue` already classifies a matching issue as `unchanged` (round #38), and `run_apply` performs zero writes for an `unchanged` action, so a rerun against unchanged findings writes nothing. A finding that stops reproducing closes its issue with an explanatory comment rather than deleting it, preserving the fingerprinted history.

An agent MUST NOT run `--apply` against a real repository without the repository owner's explicit request naming that repository -- opting a repository in via `[fleet].sync-issues = true` is itself that consent for future scheduled runs, but a first `--apply` run against a newly-enrolled repository should still be announced. Never export a GitHub token into a shared shell environment to run this command; use the narrowest-scoped invocation available.
