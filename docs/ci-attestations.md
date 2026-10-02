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

## Commands

| Command | Use |
| --- | --- |
| `ci-lint local-gate run` | runs the lanes and stamps `Local-Gate:` plus one `Ci-Attestation:` per gate of each passed lane |
| `ci-lint attest verify [--commit X] [--base REV]` | the host or CI checks a commit's trailers: valid, missing (not run), or why not |
| `ci-lint attest lineage [--commit X] [--pr N]` | prints the commit's label |
| `ci-lint attest keys --pr N --out-dir D --github-output` | side files and cache keys for the valid gates (`key_<i>`, and `stem_<i>` for `${{ env.PR_CACHE_TAG }}`) |
| `ci-lint attest resolve --family F [--require-gate G] --github-output` | the nearest ancestor entry of a cache family to restore |
| `ci-lint local-gate lint` | GATE-010 static checks: the definition parses, its lanes exist, skip jobs are mapped |
