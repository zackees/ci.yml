# Rust repository CI policy

Apply this policy with the [general code repository policy](policy-general.md). Classify each Rust repository by the artifacts it ships. A workspace can contain both libraries and applications, and should satisfy both relevant subsections.

## Rules for every Rust repository

- The quick `ci.yml` gate runs the repository's declared formatting, compile-bearing lint, and focused test checks on Linux. Use `zackees/setup-soldr` to provision Soldr and run compile-bearing Rust checks through Soldr where the dependency graph permits it. Flag pinned manual Soldr downloads or installs without `setup-soldr` for review; a pin by itself is not a defect.
- When a repository uses Dylint, run the intended workspace lint set once on Linux through Soldr. `soldr cargo dylint --all --workspace` is a reference form; `soldr dylint` or another Soldr form is acceptable when its arguments and observed coverage are equivalent. Direct `cargo dylint`, compiling `cargo-dylint` or `dylint-link` during each job, and duplicate native Dylint jobs are violations unless an approved exception explains them.
- Verify that the Linux lint job exercises platform-specific lint cases that the repository intends to enforce. Install the pinned nightly `rust-std` components for checked targets, and confirm the actual target checks. A Linux runner alone does not prove Windows or macOS source coverage.
- Keep Dylint driver, library, and output cache identities consistent with the toolchain identity used by Soldr and `setup-soldr`. A passing lint result must not silently skip saving reusable outputs. Do not copy restored files over read-only cache trees. Measure cold new-commit runs separately from warm exact-commit reruns.
- `cargo fmt` is formatting rather than a compile-bearing lint. Clippy, if required by the repository profile, is a separate check and should use Soldr. Do not interpret the Dylint rule as permission to omit Clippy or formatting.
- Keep full native platform builds and tests where they protect real behavior, but place them outside the quick `ci.yml` gate and declare whether they block merges.
- For distributed native Linux apps, prefer musl and build musl artifacts by default for supported architectures. Add glibc 2.17 compatibility artifacts wherever the dependency/toolchain graph permits; an infeasible target needs a scoped central exception. Prove the glibc symbol floor and compatible runtime behavior, and prove musl linkage/runtime behavior. A Linux runner or successful Cargo build alone does not establish artifact compatibility.
- Declare supported Windows artifacts by both architecture and ABI. GNU and MSVC, and x64 and ARM64, are independently verified variants; cross-compilation does not prove native runtime behavior. Do not invent unsupported architecture/ABI combinations to fill a matrix.

| Rule ID | Mechanical signal |
| --- | --- |
| `RUST-001` | Compile-bearing Rust CI bypasses Soldr, or manually pins/installs Soldr without `setup-soldr`, absent an approved dependency exception. |
| `RUST-002` | Dylint bypasses Soldr, builds tools repeatedly, or duplicates its intended lint set across native jobs. |
| `RUST-003` | Intended target-specific Dylint checks or pinned target `rust-std` components are missing. Resolve target behavior before confirming. |
| `RUST-004` | A successful Dylint run fails to save reusable output or cache identities diverge; requires runtime evidence. |
| `PKG-001` | A PyPI app lacks a clean installation and smoke test of a Linux wheel in the quick gate. |
| `PKG-002` | An npm app lacks a clean installation and smoke test of the packed Linux artifact in the quick gate. |
| `BIN-001` | A distributed native Linux app lacks its required musl artifact or feasible glibc 2.17 variant, or lacks compatibility evidence/approved exception. |
| `BIN-002` | A required Windows architecture/ABI artifact is missing, or a different GNU/MSVC or x64/ARM64 case is counted in its place. |

The failure cases and cache details are tracked in [issue #1](https://github.com/zackees/ci.yml/issues/1). The `running-process` preflight describes a circular dependency with Soldr; evaluate that as a scoped exception while preserving its lint coverage and timing evidence.

## Rust libraries

A library profile covers crates whose primary shipped artifact is an API consumed by another project. The quick gate should check workspace formatting, compile-bearing lint, unit tests, and the public API or documentation tests the project declares essential. Test supported feature combinations in the quick gate only when they fit its timing budget; run broader feature, target, and minimum supported Rust version matrices in separate workflows. Preserve platform-specific tests when the library's behavior depends on them.

Do not require a wheel, npm tarball, or release build from a library that does not publish such an artifact. A crate published alongside a Python or npm package also follows the application package checks below.

## Rust applications published to PyPI and/or npm

An application profile covers a Rust executable or extension delivered to users through a Python wheel, an npm package, or both. In addition to the common Rust checks, the quick Linux gate must test the **installed artifact**, because source-level Cargo tests do not prove packaging works.

For app release coverage, keep `manylinux`/glibc and `musllinux`/musl wheels distinct, and record the libc/ABI of native binaries embedded in npm packages. The proposed release cycle validates the exact staged artifacts before posting them publicly.

### PyPI package

- For a Rust-backed Python package, use Soldr as the `pyproject.toml` build backend when supported. Flag direct Maturin as the build or install backend for review; retain a documented exception where Soldr cannot be used, such as a dependency cycle.
- Build one representative Linux wheel in the quick path, install it in a clean environment, and smoke-test the import, native module, and exposed command-line entry point where present. Run Ruff and Pylint on the Python sources under the general policy.
- Build the full supported wheel matrix and run platform-specific installation tests in separate release or slower validation workflows. Do not publish to PyPI from an ordinary PR.

### npm package

- If the Rust application ships through npm, create the Linux package artifact in the quick path, install the resulting `npm pack` tarball in a clean test project, and smoke-test the exported command or module and bundled native binary where present. Check the package contents rather than relying only on `cargo test` or source-tree JavaScript tests.
- Keep Windows/macOS binary packaging, broad Node version matrices, and publication in separate validation or release workflows. Do not publish to npm from an ordinary PR.

When an app ships through both registries, test both package surfaces. A repository whose npm package is only an editor extension or wrapper should declare which published artifact is covered and which check supplies its smoke test; do not assume every `package.json` is a Rust package.
