# C/C++ (CMake) repository CI policy

Apply this policy together with the [general code repository policy](policy-general.md). A repository that also builds Rust applies the [Rust policy](policy-rust.md) to its Rust code; the two profiles stack.

This profile came from [zackees/ci.yml#393](https://github.com/zackees/ci.yml/issues/393), written while planning obs-rust/obs-studio's adoption of the local gate (OBS Studio: about 450k lines of C/C++, built with CMake and Ninja, plus a small Rust workspace). The attestation core (`GATE-001`..`GATE-010`, the lane cache, `ci-attestations.yml`, `GEN-021` reuse, the cache audit) is language-agnostic. The rules below fill the places where it assumed Rust or Python. The "Status" column says what `ci_lint` checks today. A **Candidate** row is policy that no checker enforces yet.

## Rules for every C/C++ repository

- **Gate names.** C/C++ gates in `ci-attestations.yml` are `cpp/<platform>/<check>`. The platform is `all`, `<os>-<arch>` (`linux-x64`, `windows-arm64`, `macos-universal`, ...; os is one of linux, windows, macos, freebsd, android, ios; arch is one of x64, x86, arm64, armv7, riscv64, universal), or a target triple. Examples: `cpp/linux-x64/ctest`, `cpp/all/clang-format`, `cpp/linux-x64/tier2`. Do not file C/C++ gates under `general/`.
- **Lane tools.** A local-gate lane that builds or tests C/C++ declares its tools (`tools = ["cmake", "ninja"]`, `["ctest"]`, `["clang-format"]`, ...). The lane key then covers the C/C++ pins below and, for a compiling lane, the compiler identity. A lane that only drives a container (`tools = ["docker"]`) declares no recognized tool, so it gets every ecosystem's pins, including the C/C++ ones. Its compiler lives in the image, and the image tag belongs in the lane's argv or `env`.
- **Lane pins.** A lane declaring a C/C++ tool cannot exclude its configure inputs: `CMakeLists.txt` (anywhere), `CMakePresets.json`/`CMakeUserPresets.json`, `cmake/**` and `**/*.cmake`, `vcpkg.json`/`vcpkg-configuration.json`, `conanfile.*`, `buildspec.json` (the OBS-style dependency manifest), `.gitmodules`, `.clang-format`, `.clang-tidy` and `.gersemirc`. An exclusion glob that covers one is overridden at key time, the same way as `Cargo.lock` for a Rust lane.
- **Compiler identity.** A compiling C/C++ lane (any C/C++ tool except the format-only `clang-format` and `gersemi`) also keys on `cc --version` and `c++ --version`, so a compiler upgrade reruns it. The lane does not have to name the compiler. A missing compiler records `missing`, so installing one later also changes the key.
- **Object caches.** Cache the compiler's object store, never the build tree. `[cache.family.<id>] via = "ccache"` or `via = "zccache"` declares the store (`CCACHE_DIR`/`ZCCACHE_DIR`), saved through the sanctioned `actions/cache` wrapper under a ci-lint key, `<family>-v1-<components>` from `ci-lint cache key`. Writer rules are the same as for every base layer: save on a `[cache].write-on` flow (the default-branch push, normally) and restore on PRs. `cache save-ok` refuses a PR-flow base save (`CACHE-008` rule 2), and `CACHE-004` budgets these families like any other. A family that PRs restore and no writer ever saves is the `CACHE-027` failure (Candidate): every PR starts cold.
- **No build trees in caches.** A CMake build tree (`build/`, `build-<x>/`, `build_<x>/`, `cmake-build-<x>/`, `out/build/<preset>/`, anything under `CMakeFiles/`, `CMakeCache.txt`) is per-run output bound to absolute paths and configure state. It is never a cache payload (`CACHE-007`, `CACHE-008` rule 11). `build/.ccache` (an object store kept inside the build directory) is allowed. The build directory itself is not.
- **Shell budget.** `GEN-005` allows one command per `run:` step. A CMake configure (`cmake -S <src> -B <dir>`, `cmake --preset <name>`), `cmake --build` or `ctest` invocation may be wrapped over several lines with trailing-backslash continuations, because configure lines carry many `-D` flags. Shell syntax is still banned inside it: use `--parallel` or `ctest -j` (or `CMAKE_BUILD_PARALLEL_LEVEL`/`CTEST_PARALLEL_LEVEL` in `env:`) instead of `-j $(nproc)`.
- **Parallel tests.** `ctest` runs one test at a time unless told otherwise. Every CI `ctest` run passes `-j` (CMake 3.29 and later: one job per core), `--parallel <N>`, or `-j<N>`, or runs with `CTEST_PARALLEL_LEVEL` set, or uses a test preset with `execution.jobs` (`CPP-001`). Tests that cannot share a host get a `RESOURCE_LOCK` or `RUN_SERIAL` property. The whole suite does not go serial for them.

### Format lanes are keyed on the files they format (pattern; `CPP-002`, Candidate)

clang-format and gersemi only read the files they format plus their config. A whole-tree format lane reruns on every docs-only or Rust-only change. Key the lane on its own file set instead. Lane inputs are decided by exclusion, so exclude everything and `keep` the formatted files:

```toml
[gate.lanes.clang-format]
run = ["python3", "ci/local_gate.py", "--lane", "clang-format"]
tools = ["clang-format"]
exclude = ["**"]
keep = ["**/*.c", "**/*.h", "**/*.cpp", "**/*.hpp", "**/*.m", "**/*.mm"]

[gate.lanes.gersemi]
run = ["python3", "ci/local_gate.py", "--lane", "gersemi"]
tools = ["gersemi"]
exclude = ["**"]
keep = ["**/CMakeLists.txt", "**/*.cmake"]
```

Mandatory inputs survive `exclude = ["**"]`: the gate declaration, every repository file the gate's or the lane's argv names (`ci/local_gate.py`), and the C/C++ pins (`.clang-format`, `.gersemirc`, ...). A config or formatter-script change still reruns the lane. Declaring a format tool adds the C/C++ pins but not the compiler identity. Prove the file set with `ci-lint local-gate lanes --audit <lane>` (`GATE-007`, strace-based): an excluded file the formatter opens is reported. Run the formatter on the whole kept set, not on "changed files". The lane key already skips an unchanged set, and a changed-files run cannot prove the tree is formatted.

Candidate signal: a lane that declares only `clang-format`/`gersemi` and keeps the whole tree.

### Native-platform legs: Linux attests, native legs run on push, `ci-full` or a path filter (pattern; `CPP-003`, Candidate)

C/C++ repositories commonly have sources that compile only on one OS: D3D11/WinRT on Windows, Metal/Cocoa on macOS. A Linux local gate cannot prove them, and a Linux attestation must never skip them.

- The quick gate is Linux. `ci-attestations.yml` maps Linux gates (`cpp/linux-x64/...`) only to Linux jobs. Windows and macOS build/test jobs are not in `[gate.trust].skip` and are listed in `verify-exempt`, so they start at once instead of waiting on the verify job.
- Native legs run on every default-branch push (`GATE-008`: a push never skips), on PRs labelled `ci-full`, and on PRs whose paths touch platform implementation directories (for OBS: `libobs-d3d11/**`, `libobs-winrt/**`, `libobs-metal/**`, `plugins/win-*/**`, `plugins/mac-*/**`, `**/*.mm`, `**/*.m`, `cmake/windows/**`, `cmake/macos/**`). Select them per the general policy's "Selecting full PR coverage": changes to callers or shared code do not select them.
- A cross-compile or emulation lane (mingw on Linux, Wine) may prove `build` but never `test` (`GATE-011`: emulation never proves a `test` check). Name such gates by the target they build, for example `cpp/windows-x64/build` with `fidelity: emulation`.

Candidate signal: a `cpp/<windows|macos>-*/...` gate that a Linux-only lane attests at `native` fidelity, or a native job listed in `[gate.trust].skip`.

### Header-aware lane inputs (`CPP-004`, Candidate)

Whole-tree lane keys are safe but coarse for a large tree: any `libobs/` edit reruns every C test. A lane could derive its inputs from `compile_commands.json` plus Ninja depfiles (`ninja -t deps`) to get header-aware keys. That needs the same `--audit` proof as today's exclusions, and a fail-closed path when the dependency database is stale. It is out of scope for #393's first slice. Until then, use exclusions proven with `--audit`.

## Rule catalog

| Rule ID | Mechanical signal | Status |
| --- | --- | --- |
| `GATE-010` (`cpp` ecosystem) | A `ci-attestations.yml` gate path `cpp/<platform>/<check>` whose platform is not `all`, `<os>-<arch>` or a target triple; any ecosystem outside `rust`/`python`/`cpp`/`general`. | Enforced by ci-lint (static, `ci_lint.attestations.validate_gate_path`, wherever `ci-attestations.yml` is loaded: `local-gate run`/`lint`/`verify`, `attest verify`) |
| `GATE-007` (C/C++ pins) | A lane declaring a C/C++ tool gets `CMakeLists.txt`, `CMakePresets.json`, `CMakeUserPresets.json`, `cmake/**`, `**/*.cmake`, `vcpkg.json`, `vcpkg-configuration.json`, `conanfile.*`, `buildspec.json`, `.gitmodules`, `.clang-format`, `.clang-tidy`, `.gersemirc` as mandatory key inputs, and a compiling lane also keys on `cc --version`/`c++ --version`. | Enforced by ci-lint (runtime, `ci_lint.lane_cache.ECOSYSTEM_PINS`/`key_tools`, in `local-gate run`) |
| `CT-002` (`via = "ccache"`/`"zccache"`) | A `[cache.family]` `via` outside the recognized set. `ccache`/`zccache` are accepted and keyed `<family>-v1-<components>` by `ci-lint cache key`. | Enforced by ci-lint (static, schema load) |
| `CACHE-004` (object-cache families) | The declared families, including `ccache`/`zccache`, exceed `[cache].budget`. | Enforced by ci-lint (static precheck) |
| `CACHE-007` (CMake build tree) | A cached path is a CMake build tree (`build/`, `build-*/`, `build_*/`, `cmake-build-*/`, `out/build/*`, `CMakeFiles/`, `CMakeCache.txt`). | Enforced by ci-lint (static precheck on `actions/cache*` `path:`; runtime `ci-lint cache payload-check --manifest`) |
| `CACHE-008` rule 11 | `ci-lint cache save-ok <family> --path <p>...`: a save whose paths include a CMake build tree is refused. Rule 2 (base layers save only on `[cache].write-on` flows) applies to `ccache`/`zccache` families unchanged. | Enforced by ci-lint (runtime, `ci_lint.cache.save_ok`) |
| `CACHE-010` (`CCACHE_DISABLE`) | `CCACHE_DISABLE` set truthy in a workflow, job or step `env:` while `ci.toml` declares a `via = "ccache"` family. | Enforced by ci-lint (static precheck) |
| `CACHE-027` (object caches) | A `ccache`/`zccache` family that PRs restore and no writer flow ever saves. | Candidate (the general `CACHE-027`) |
| `GEN-005` (C/C++ allowlist) | A `run:` step that is more than one command or uses shell syntax. One `cmake -S/-B`/`cmake --preset`, `cmake --build` or `ctest` command may span trailing-backslash continuation lines. | Enforced by ci-lint (static precheck, `ci_lint.rules.shell.cpp_allowlisted_command`) |
| `CPP-001` | A CI `ctest` invocation (a `run:` line, one level into a referenced `ci/*.py` script, or, from `local-gate lint`, a `ci/*.py` script the local gate names) with no `-j`/`-j<N>`/`--parallel [<N>]`, no `CTEST_PARALLEL_LEVEL` (inline, step/job/workflow `env:`, or exported through `$GITHUB_ENV` in the same job) and no test preset `execution.jobs`, or one pinning the level to 1. A `--preset` missing from `CMakePresets.json`, or a script that builds its `ctest` argv dynamically with no parallelism flag or `CTEST_PARALLEL_LEVEL` in sight, is `needs_review`. `ctest -N`/`--show-only` is not a finding. A same-line `# ci-lint: allow CPP-001 <reason>` excuses one line. | Enforced by ci-lint (static precheck and `local-gate lint`, `ci_lint.rules.cpp_ctest`) |
| `CPP-002` | A format-only lane (`clang-format`/`gersemi`) keyed on the whole tree instead of its own file set. | Candidate (documented pattern above) |
| `CPP-003` | A Windows/macOS `cpp/...` gate attested by a Linux lane at `native` fidelity, or a native-platform job in `[gate.trust].skip`. | Candidate (documented pattern above; `GATE-011` already refuses emulation proving `test`) |
| `CPP-004` | Header-aware lane inputs from `compile_commands.json` plus Ninja depfiles, proven by `--audit`. | Candidate (out of scope for #393's first slice) |
