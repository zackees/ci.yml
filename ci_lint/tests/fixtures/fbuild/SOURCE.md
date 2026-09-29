# fbuild coverage-preservation fixture

Follow-up from [zackees/ci.yml#39](https://github.com/zackees/ci.yml/issues/39)
(sub-issue of #4/#52) and proposal.md's "Explicit acceptance fixture:
`fbuild-coverage-preserved`" section. Proves `existing required coverage ⊆
declared coverage` for a repository shaped like `FastLED/fbuild`, so that
removing a board case, a macOS architecture, or the ignored Python facade
suite is a specific, machine-detected regression -- not a claim resting on
a runner label (AGENTS.md: "Confirm coverage from commands and runs, not
from a runner label").

## Pinned snapshot

Source: `FastLED/fbuild` @ `ef10ced8c86c124af72266e2a7041497c8709a66`
(read-only, fetched via `gh api repos/FastLED/fbuild/contents/<path>?ref=<sha>`
-- no clone, no write access to that repository per the worker contract).

Files copied verbatim (only what the coverage check needs, kept small):

- `board_families.json` <- `ci/board_families.json` at the pinned SHA.
  The real per-board CI registry: **80 entries**, of which **4 are
  duplicate `workflow` aliases sharing one `env_name`**
  (`build-nano-every.yml`/`build-nano_every.yml` -> `nano_every`;
  `build-nucleo-f429zi.yml`/`build-nucleo_f429zi.yml` -> `nucleo_f429zi`;
  `build-nucleo-f439zi.yml`/`build-nucleo_f439zi.yml` -> `nucleo_f439zi`;
  `build-uno-r4-wifi.yml`/`build-uno_r4_wifi.yml` -> `uno_r4_wifi`),
  giving **76 distinct board build cases** by `env_name`. Verified locally:
  `python3 -c "import json; d=json.load(open('board_families.json'));
  b=d['boards']; print(len(b), len(set(x['env_name'] for x in b)))"` ->
  `80 76`.

## Non-board required coverage (evidence, not copied verbatim -- too large)

Read directly from the pinned SHA with `gh api
repos/FastLED/fbuild/contents/<path>?ref=ef10ced8c86c124af72266e2a7041497c8709a66`:

- **Both macOS architectures**: `.github/workflows/check-macos.yml`'s
  `test` job has `strategy.matrix.runner: [macos-15-intel, macos-15]` --
  two real runtime cases (`soldr cargo test --workspace` on each), not one
  job on a shared "macOS" label.
- **Ignored Python facade tests**: `.github/workflows/check-ubuntu.yml`'s
  `python-facade-tests` job runs `soldr cargo test -p fbuild-python --test
  python_facades -- --ignored` -- the embedded-CPython AT-P integration
  suite is `#[ignore]`d out of the default `soldr cargo test --workspace`
  sweep (FastLED/fbuild#1485 §5.2/§6) and only executed here, under a
  pinned interpreter. `audit-ignored-tests.yml` is a **separate**, weekly
  reporting workflow that updates tracking issue #880 -- proposal.md item
  7: "a reporting workflow that updates an issue is not a test unit," so
  it is explicitly excluded from required coverage.
- **Full-graph gate / standalone required checks**: `check-ubuntu.yml`'s
  `check` job (`soldr cargo check --workspace --all-targets`, `soldr cargo
  clippy --workspace --all-targets -- -D warnings`, `soldr cargo test
  --workspace`, plus the two stdlib Python guard scripts
  `ci/find_direct_subprocess.py --fail` and
  `ci/check_usb_vidpid_literals.py`).

## Regenerating / verifying the snapshot

```
gh api repos/FastLED/fbuild/contents/ci/board_families.json?ref=ef10ced8c86c124af72266e2a7041497c8709a66 --jq '.content' | base64 -d
```

Re-running that command must reproduce `board_families.json` byte for
byte (the SHA is pinned, so it always will unless GitHub content-encodes
differently). This fixture does not re-fetch at test time -- it is a
frozen, offline snapshot so `ci_lint`'s test suite stays standard-library
-only and network-free.
