# ci.yml

Policy and planned automation for fast, reliable pull request CI across the `zackees`, `FastLED`, and `TechWatchProject` repositories. The checker and scheduler are not implemented yet.

## Decision

Standardize each code repository on `.github/workflows/ci.yml` as the entry point for its required quick PR checks. A central checker will evaluate that workflow against a declared repository profile, inspect historical run timings, and open or update one issue per confirmed violation. A finding is resolved by fixing the repository or by changing the policy or recording a reviewed exception.

This combines a predictable CI interface with measured evidence. Shared reusable workflows can implement common jobs, but their use alone does not prove that required checks ran or that PR feedback was fast.

## Policy at a glance

| Area | Fleet rule |
| --- | --- |
| Quick gate | `ci.yml` runs the required checks for ordinary PRs on Linux. Windows, macOS, packaging matrices, and long integration runs belong in separate workflows when needed. |
| Coverage | A faster gate must preserve the checks required by the repository's profile; removing a workflow is not evidence of an improvement. |
| Native Linux artifacts | Prefer musl and build musl by default for supported architectures. Also target glibc 2.17 where feasible; verify the artifact's compatibility, or track a scoped exception. |
| Windows artifacts | Track GNU versus MSVC and x64 versus ARM64 as distinct target cases. A build for one combination cannot satisfy another. |
| Rust | Use `setup-soldr` and Soldr for compile-bearing Rust checks where the dependency graph permits it. Run Dylint once on Linux and verify intended platform-specific lint coverage. |
| Python | Run Ruff formatting/import checks and Pylint on the relevant Python paths. Avoid duplicate Black or isort jobs. |
| Python benchmark data | Use dataclasses as the internal model for benchmark inputs, results, and reports; never pass raw dictionaries through benchmark code. JSON and Protocol Buffers are allowed on the wire. Type boundary dictionaries with concrete value types, never `Any`; for a list of records, use at least `list[dict[str, TypedData]]` with a declared concrete `TypedData` type before converting and validating each record into a dataclass. |
| Rust apps published to PyPI or npm | Test the installed Python wheel or packed npm artifact in the quick Linux gate; keep full platform artifact matrices in release or slower validation workflows. |
| Timing | Measure required PR critical path, runner queue, job and step execution, and known cold/warm cache behavior separately. Do not file a performance violation from one outlier. |
| Exceptions | Record the rule, reason, owner, and review date. An undocumented exception does not silently satisfy the policy. |

The exact Dylint command is not a text-match rule. For example, `soldr cargo dylint --all --workspace` and an equivalent `soldr dylint` invocation can satisfy the policy if they cover the intended workspace and targets. Direct Dylint tool installation and duplicate native Dylint jobs are candidates for review.

## Evidence behind the policy

- [Issue #1](https://github.com/zackees/ci.yml/issues/1) records Dylint cache identity drift, cold versus warm timing, missing cross-target toolchain components, and platform coverage risks.
- [Issue #2](https://github.com/zackees/ci.yml/issues/2) inventories slow PR paths, current workflow violations, already fixed cases, and gaps in the sampled fleet. Its findings include `running-process`, `fbuild-ide`, `reld`, and historical `datalake-core` runs.
- [Issue #4](https://github.com/zackees/ci.yml/issues/4) proposes the composable CI contract, full coverage checks, release candidate gate, Linux libc and Windows ABI target model, and fbuild acceptance fixture.
- The sampled Rust workflows did not reveal a pinned manual Soldr installation without `setup-soldr`. The checker should still detect that pattern without asserting it exists fleet-wide.

## Documentation

- [General code repository policy](docs/policy-general.md): quick gate, coverage, timing, and exceptions.
- [Rust repository policy](docs/policy-rust.md): common Rust checks, libraries, and apps published to PyPI or npm.
- [Agent operating guide](docs/agent-guide.md): mechanical scan, evidence, issue lifecycle, and regression cases.
- [Detailed proposal](proposal.md): proposed schema, repository inventory, target variants, release lifecycle, and acceptance tests.
- [AGENTS.md](AGENTS.md): index and instructions for agents working in this repository.

## Status

These files define the intended policy and checker behavior. Repository profiles, the scanner, scheduling, and automated issue management are future implementation work. Do not treat the documented checks as already enforced.
