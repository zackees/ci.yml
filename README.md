# ci.yml

An AI-assisted checker for slow CI in the `zackees`, `FastLED`, and `TechWatchProject` repositories. It runs on a slow, scheduled loop, inspects recent CI runs and workflow configuration, and prepares focused fixes for avoidable build and lint time.

This repository currently defines the policy and intended behavior. The checker implementation and scheduler have not been added yet.

## First priority: fast Dylint validation

Rust repositories should have `.github/workflows/ci.yml` as their quick validation workflow. When a repository uses Dylint, that workflow's fast path should run all workspace lints through Soldr on a Linux runner:

```sh
soldr cargo dylint --all --workspace
```

The Linux Dylint job should cover the platform-specific lint cases as well as the common ones. Repeating Dylint compilation in separate macOS, Windows, or platform-matrix jobs is a slow configuration to flag. Platform builds and tests may still run where needed in separate workflows; the quick `ci.yml` path should stay on Linux.

The checker should also flag direct `cargo dylint` invocations, custom Dylint bootstrapping, duplicate lint jobs, and other nonstandard mechanisms that bypass Soldr. It should compare recent job durations and compile logs with the repository's own history so unusually long or repeated Dylint compile cycles become visible. Before proposing a change, it should verify that the Linux job actually exercises every intended platform lint and that the replacement command passes.

## Python in the fast path

If a repository contains Python, the quick `ci.yml` validation should run both Ruff and Pylint. Ruff should handle formatting and import ordering through `ruff format` and the `I` rules in `ruff check`. CI should not invoke Black or isort as separate tools.

A representative validation is:

```sh
ruff check .
ruff format --check .
pylint <project-python-paths>
```

Each repository should select the Python paths and Pylint configuration that match its source tree. The checker should confirm Ruff's import sorting rules are enabled and that formatting is checked, then remove redundant Black or isort jobs only after the equivalent Ruff checks pass.

## Bad patterns to avoid

- Running Windows or macOS runners from `.github/workflows/ci.yml`. Keep the normal PR validation path on Linux; put necessary platform builds and tests in separate workflows.
- Running Rust lint checks without `soldr cargo dylint --all --workspace`. Other Rust lint paths can trigger slow, repeated compilation.
- Making normal PRs wait on excessive or slow CI pipelines. Keep the required quick checks focused and move longer validation out of the normal PR path.
- In Rust projects with Python bindings, using an install step that invokes Maturin directly instead of running it through Soldr.

## Slow-loop workflow

1. Periodically inventory the configured repositories and their recent `ci.yml` runs.
2. Read workflow files, lint configuration, job logs, and timing history. Record the actual slow step and the evidence for it.
3. Prioritize Dylint duplication, missing Soldr adoption, and missing fast-path coverage. Then inspect Python lint duplication and other repeatable CI delays.
4. Make a small change in the affected repository, run the relevant quick validation, and compare its result and duration with the baseline.
5. Open a reviewable pull request with the measurements, the coverage preserved, and any uncertainty. Recheck subsequent runs before treating the issue as resolved.

The loop should avoid speculative rewrites: a faster workflow is only a fix when the required lints still run and pass. Timing comparisons should distinguish cold and warm caches and account for ordinary run-to-run variation.

## Status

Policy and scope are documented here. Automated discovery, model execution, scheduling, and repository changes are future work.
