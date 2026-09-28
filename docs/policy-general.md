# General code repository CI policy

This policy applies to active code repositories under `zackees`, `FastLED`, and `TechWatchProject`. It specifies the planned fleet contract; enforcement has not been implemented.

## Required quick PR gate

- Use `.github/workflows/ci.yml` as the discoverable entry point for ordinary `pull_request` validation. It may call reusable workflows or repository scripts. Migration of an existing workflow name is a tracked change, not grounds to claim its checks are absent before inspecting them.
- Keep required jobs in this quick gate on Linux. Put necessary Windows/macOS builds, device tests, full package matrices, and long integration or end-to-end suites in separate workflows. A separate workflow may still run on PRs when its coverage is required; record whether it is a required merge check and include it in PR timing.
- Declare the repository's profile and required check names in the future central fleet inventory. Profiles describe the tests that must run; the existence of `ci.yml` alone does not satisfy coverage.
- Keep check names stable so required-check settings and timing comparisons remain meaningful. A skipped or removed required check needs explicit review.
- Reusable workflows are encouraged where they reduce duplication. Inspect both the caller and called workflow before deciding what a job actually runs.
- Track execution hosts separately from artifact targets. Linux x64 musl, Linux x64 glibc 2.17, Windows x64 GNU, and Windows x64 MSVC are distinct target cases even when a Linux runner cross-builds them. ARM64 variants are likewise distinct. The proposed target contract and release gate are detailed in [proposal.md](../proposal.md).

## Language checks

- Python sources in the quick path run Ruff checks, including import sorting (`I` rules), `ruff format --check`, and Pylint with the repository's source paths and configuration. Remove standalone Black or isort checks only after equivalent Ruff checks pass.
- Model Python benchmark inputs, results, and reports with dataclasses internally; do not pass raw dictionaries between benchmark code. JSON and Protocol Buffers are valid wire formats. At the serialization boundary, type dictionaries with declared, concrete value types; `dict[str, Any]`, untyped `dict`, and equivalent `Any`-valued aliases are not permitted. A list of record dictionaries must be typed at least as `list[dict[str, TypedData]]`, where `TypedData` stands for a declared concrete value type or union. Convert and validate decoded wire data into dataclasses before using it, and serialize dataclasses only when crossing the boundary.
- Test benchmark model validation and wire-format round trips so missing, mistyped, or changed fields fail clearly instead of silently breaking consumers.
- Other languages should declare their fast lint and test commands in the profile. Do not infer that an unrecognized workflow has adequate coverage.
- Apply the additional [Rust policy](policy-rust.md) when the repository builds Rust code.

## Performance rule

The initial candidate threshold is **at least five completed ordinary PR samples in a rolling 30-day window**, with either a **75th-percentile required job execution time over 10 minutes** or a **75th-percentile required PR critical path over 15 minutes** on two successive scans. These are policy starting points to calibrate against fleet data, not claims about current fleet performance. A single severe outlier can be sent for review, but should not automatically become a confirmed timing violation.

Measure the elapsed time from the first required workflow's creation to the last required check's completion as the PR critical path. Also report per-workflow, job, and step durations. Keep runner queue time separate from execution time; parallel workflow durations must not be added together. Label cache state as cold, warm, or unknown only when run evidence supports the label. Compare new commits with new commits and exact-commit reruns with reruns.

## Rule and exception decisions

The checker should classify each result as **pass**, **violation**, **needs review**, **approved exception**, or **insufficient evidence**. Missing quick gates, native runners in `ci.yml`, absent required checks, and duplicate lint work are structural candidates. Slow PRs require the timing evidence above. A parser that cannot resolve a GitHub expression or script must return **needs review**, not pass.

| Rule ID | Mechanical signal |
| --- | --- |
| `GEN-001` | No ordinary-PR `.github/workflows/ci.yml` entry point. |
| `GEN-002` | A job in `ci.yml` uses a non-Linux runner. Resolve matrices, conditions, and reusable calls before deciding. |
| `GEN-003` | A profile-required check is missing, skipped, or removed without approved coverage replacement. |
| `GEN-004` | Python quick checks lack Ruff import/format validation or Pylint, or duplicate Black/isort work. Applies only when the profile contains Python. |
| `PY-001` | Python benchmark inputs, results, or reports use raw dictionaries as internal models instead of dataclasses; boundary dictionaries use `Any` or lack concrete value types (including lists of records); or wire data is used without conversion and validation at the boundary. |
| `PERF-001` | Repeated PR timing breach under the sample and threshold rule above, with queue and execution reported separately. |

An exception must name the repository, rule ID, reason, owner, compensating coverage, and review date. The documented Soldr dependency cycle in `zackees/running-process` is an example to evaluate for an exception. Exceptions are reviewed when their date arrives or the dependency changes; they do not erase historical findings.

Each confirmed violation gets one issue keyed by repository, rule ID, and workflow or job identity. Update that issue with new evidence rather than creating duplicates. Close it only after the repository passes the rule on current configuration and required checks still run, or after a reviewed policy change or exception is recorded.

## Priority examples from the inventory

- [`zackees/running-process`](https://github.com/zackees/ci.yml/issues/2#issuecomment-5859678465): long platform preflights; its Soldr dependency constraint needs explicit treatment.
- [`FastLED/fbuild-ide`](https://github.com/zackees/ci.yml/issues/2#issuecomment-5859690489): package and end-to-end work on both Linux and Windows in normal PR CI.
- [`zackees/reld`](https://github.com/zackees/reld/blob/main/.github/workflows/ci.yml): direct Dylint setup and native platform jobs in `ci.yml`.
- [`TechWatchProject/datalake-core`](https://github.com/zackees/ci.yml/issues/2#issuecomment-5859676406): historical slow builds, followed by missing test workflows on current `main`; verify coverage before treating the timing problem as fixed.
