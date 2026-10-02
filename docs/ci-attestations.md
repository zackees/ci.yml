# ci-attestations: per-gate evidence in commit messages (GATE-010)

**Status:** candidate GATE-010, phase 1 implemented in `ci_lint` and piloted in zackees/soldr. Policy issue: [#198](https://github.com/zackees/ci.yml/issues/198); soldr companion: [soldr#3534](https://github.com/zackees/soldr/issues/3534). This document is the format reference; the binding text is in [policy-general.md](policy-general.md#per-gate-attestations-gate-010).

## Goal

Systematize the pattern of writing attestations into PR content. An attestation lives in the **commit message**, never in the source tree. Each one is validated on the host that ran the gate and published with the commit.
- **Definition:** `ci-attestations.yml`, YAML with comments allowed.
- **Transit:** compact JSON, one `Ci-Attestation:` trailer per gate.

CI then skips each remote job whose gates are all attested. As a side effect it also records which commits are known good, which tells it which caches are valid to hydrate.

## The definition: `ci-attestations.yml`

```yaml
# soldr's pilot definition (comments allowed)
version: 1
gates:
  rust/all/fmt: {lane: rust}
  rust/x86_64-unknown-linux-gnu/clippy: {lane: rust}
  rust/x86_64-unknown-linux-gnu/dylint: {lane: rust}
  rust/x86_64-unknown-linux-gnu/test: {lane: tests}    # nextest + doctests (glibc)
  rust/x86_64-pc-windows-msvc/clippy: {lane: cross}    # extra coverage: no PR job runs this
jobs:
  ci.yml:build-linux-x64:
    - rust/all/fmt
    - rust/x86_64-unknown-linux-gnu/clippy
    - rust/x86_64-unknown-linux-gnu/dylint
    - rust/x86_64-unknown-linux-gnu/test
```

- **Gate path:** `<ecosystem>/<platform>/<check>`. The ecosystem is `rust`, `python` or `general`. For Rust, the platform is a target triple or `all`.
- **`lane`:** the local-gate lane (GATE-007) whose pass proves the gate.
- **`jobs`:** maps a remote job (`<workflow>:<job id>`) to the gates it runs. A job can be skipped only when every one of its gates is attested.
- **Restricted YAML** (`ci_lint.mini_yaml`, standard library only):
  - allowed: block mappings and sequences, flow `{..}`/`[..]` of scalars, and comments;
  - rejected: anchors, aliases, tags, merge keys, block scalars, and multiple documents.

  A bare developer host and a CI runner therefore parse it identically, with nothing installed.
- **Read from the PR base** in CI (like `[gate.trust]`). The file is an automatic GATE-008 surface: a PR that changes it runs remotely.

## The attestation: compact JSON trailer

`ci-lint local-gate run` writes one trailer per declared gate whose lane passed, whether it ran or was reused from the GATE-007 cache. These sit beside the existing `Local-Gate:` trailer:

```
Local-Gate: v1 tree=5a60710e... secs=510 lanes=py-static:reused@be00dc106fad,...,tests:run
Ci-Attestation: {"at":1790917000,"gate":"rust/x86_64-unknown-linux-gnu/test","host":"linux-x86_64","key":"9f3c...","lane":"tests","parents":["6f6b886c..."],"secs":506,"stamp":"3b0e...","tree":"5a60710e...","v":1,"via":"run"}
```

- **Omission means not run.** A declared gate with no trailer runs remotely. There is no "skipped" entry.
- **Stamp:** `sha256(tree \n parents \n canonical-json-without-stamp)`, truncated to 32 hex digits. A commit's hash covers its own message, so the stamp binds the tree and the parents instead. Editing the JSON, changing content, or rebasing invalidates it (`bad-stamp`, `wrong-tree`, `wrong-parents`). The stamp is tamper-evident; it is not a signature.
- **Size:** about 330 bytes per gate. Five gates add under 2 KiB to the commit message.

## In CI

1. **Verify job:** `ci-lint local-gate verify --trust --github-output`, with full history.
   - GATE-008's head-level policy applies first: base policy, surfaces, author, fork, `ci-full`, and the audit sample.
   - Then, per `[gate.trust].skip` job, it outputs **`skip_<job id>`**, which is `true` only when every gate the base definition maps to that job has a valid attestation.
   - Each skip job's `if:` consumes `needs.<verify>.outputs.skip_<job id>`.
2. **Push events never skip,** so the default-branch run is the post-merge catch. **Release ignores attestations, always.**
3. **Side entries for caching:** `ci-lint attest keys --pr <N> --base <sha> --out-dir D --github-output` writes each valid attestation to a tiny file. It outputs `key_<i>` / `path_<i>`, and the workflow saves each one as its own cache entry. Entries are tiny (bytes), and the repository's entry budget is large, so many are fine.
4. **Hydration:** `ci-lint attest resolve --family <cache family> [--require-gate <gate>]` lists cache keys through the REST API (GET only). It picks the **nearest ancestor** entry of that family, optionally only from commits whose side entry for the required gate exists, so a build cache is hydrated only from an attested-good state.

## Human-readable, ancestor-defining cache keys

```
<family>-m<n>-<sha10>              default-branch commit, n = first-parent ordinal on main
<family>-m<b>-c<k>-<sha10>-pr-<N>  PR #N's commit, k first-parent commits above its base m<b>
```

Examples, with real soldr values:
- `att1-rust.x86_64-unknown-linux-gnu.test-m2189-c1-18204251ce-pr-3532`
- `zccache-unit-v1-x86_64-unknown-linux-gnu-m2190-4b4f981d09`

**Why the PR component is a suffix (experiment K2).** The first draft was `pr-<N>-m<b>-c<k>-<sha10>`, after the owner's `*-pr-NN-(commit ordinal)` sketch. soldr's `check_pr_cache_keys.py` (CACHE-013) already requires every PR-reachable cache save to carry `${{ env.PR_CACHE_TAG }}`, which is exactly `-pr-<N>` in a PR and empty elsewhere. With the tag as a suffix:
- one key shape (`<stem>${{ env.PR_CACHE_TAG }}`) serves both main and PRs;
- the existing checkers and janitor accept it unchanged;
- `ci-lint attest keys` outputs both the full `key_<i>` and the `stem_<i>` a workflow appends the tag to.

What makes the labels ancestor-defining:
- **Ancestry is readable from the key alone,** without git:
  - `m<i>` precedes `m<j>` iff i < j (main is never rewritten);
  - `m<b>-c<i>-…-pr-N` can precede `m<b>-c<j>-…-pr-N` only if i < j and the bases match;
  - a main entry can precede a PR commit only if its ordinal is at most the PR's base ordinal.
- **The SHA prefix lets git confirm it** (`merge-base --is-ancestor`) when history was rewritten.

## Experiments (soldr pilot)

- **K1, ancestry across pushes.** 92 consecutive PR-head transitions (46 branches, 2026-09-25..10-02, GitHub compare API):
  - **76 (83%) were fast-forwards:** the earlier head is an ancestor, so `c<k>` ordering holds.
  - **16 diverged:** 12 rebases onto a newer main (`m<b>` moves forward, so the nearest ancestor becomes a `main-m` entry) and 4 content amends or squashes (same `m`, different SHA, caught by the SHA check).
  - **None** were message-only amends.
- **X1, cross-target Clippy on a Linux host.**
  - `soldr cargo clippy --workspace --all-targets --target aarch64-apple-darwin` passed in 111 s cold.
  - `--target x86_64-pc-windows-msvc` ran in 74 s and **found 3 real warnings on soldr `main`** (two test-only items compiled into the library, and an unused import). No ordinary PR job runs Windows Clippy, so they had gone unnoticed. Attesting "Clippy (all platforms)" locally is cheap, and it catches what PR CI cannot.
- **Resolve on soldr history:** for PR #3532 (`m2189-c1-…-pr-3532`), the resolver picks `m2189` and rejects `m2191`, which is not an ancestor. With `--require-gate …/test` and only `m2188` attested, it picks `m2188`.

## Pilot results (soldr)

| PR | GATE-008/010 decision | CI wall time |
| --- | --- | ---: |
| soldr#3537 (phase-1 wiring; surfaces changed) | `surface-changed`: both jobs ran; the publisher found no definition at the base | ~14 min |
| **soldr#3541** (first ordinary PR: the Windows Clippy fixes X1 found) | trusted; `ci.yml:lint` skip (2/2 gates), `ci.yml:build-linux-x64` skip (5/5) | **20 s** |

- `CI Pre › Publish ci-attestations` saved 7 entries of about 430 B each, e.g. `att1-rust.x86_64-unknown-linux-gnu.test-m2192-c1-59fd7786bc-pr-3541`. `ci-lint attest resolve` found them through the live REST listing (GET only).
- **The squash commit can carry valid attestations.** #3537's `main` push (d17f707) published `att1-…-m2192-d17f7071c1` on `refs/heads/main`. The PR was up to date with main, so the squash commit had the attested head's exact tree and parent, and its stamps genuinely hold. These `main`-scoped entries are readable from every branch, so they are what later PRs hydrate from. When the PR is behind main, the stamps are rejected (`wrong-tree`/`wrong-parents`). Either way, `main` pushes run every job.

## Roadmap and status

1. **Phase 2: done** (soldr#3548). The `cross` lane attests `rust/{x86_64-pc-windows-msvc,aarch64-apple-darwin}/{clippy,dylint}` from the Linux host. It passes 5/5 checks: 307 s warm, 815 s cold.
2. **Phase 3: done** (soldr#3548), with platform test lanes chosen by fidelity ([#202](https://github.com/zackees/ci.yml/issues/202)):

   | Lane | Fidelity | Attests | Measured |
   | --- | --- | --- | --- |
   | `wine` (container) | emulation | `rust/x86_64-pc-windows-msvc/unit`, for the crates that pass in full | 400 tests, about 8 s |
   | `winvm` (warm dockur/windows VM, optional) | native-equivalent | `rust/x86_64-pc-windows-msvc/test`, the target-run partition | 160/160, 400–492 s |
   | Darling | emulation with process/IPC gaps | nothing (advisory) | broker/daemon wedge; `readdir`/`copy` gaps |
   | macOS Recovery guest | native-equivalent | `rust/x86_64-apple-darwin/test` replay set (next) | kernal-api: 950 tests in 163 s |

   - A `…/test` gate takes only a native or native-equivalent lane.
   - Emulation lanes attest a distinct check name (`unit`) with an explicit crate scope.
   - **Host-optional lanes** (`optional = true`, exit 75) are never attested where they cannot run.
3. **Phase 4: done** (soldr#3549 and soldr#3550). The Tier-2 zccache store is saved under lineage keys (`…-m<n>-<sha10>`). Linux x64 restores the nearest ancestor generation whose commit has a valid `rust/x86_64-unknown-linux-gnu/test` side entry (`attest resolve --require-gate`, GET-only, advisory, prefix fallback). Verified live: `main` m2201 published both, the resolver picks `…-m2201-9f92fdbe37`, and it skips m2200, whose test gate overflowed the old 8 slots (fixed in #218: job-mapped gates are published first, and overflow is a `::warning`). Next: the Dylint trees and cross lanes.
4. **Isolation:** the isolated runner proves which tree it tested (GATE-009, `.gate-nonce`; bosn 0.1.7 fixed the container reuse at the root).

## Commands

| Command | Use |
| --- | --- |
| `ci-lint local-gate run` | runs the lanes and stamps `Local-Gate:` plus one `Ci-Attestation:` per gate of each passed lane |
| `ci-lint attest verify [--commit X] [--base REV]` | the host or CI checks a commit's trailers: valid, missing (not run), or why not |
| `ci-lint attest lineage [--commit X] [--pr N]` | prints the commit's label |
| `ci-lint attest keys --pr N --out-dir D --github-output` | side files and cache keys for the valid gates (`key_<i>`, and `stem_<i>` for `${{ env.PR_CACHE_TAG }}`) |
| `ci-lint attest resolve --family F [--require-gate G] --github-output` | the nearest ancestor entry of a cache family to restore |
| `ci-lint local-gate lint` | GATE-010 static checks: the definition parses, its lanes exist, skip jobs are mapped |
