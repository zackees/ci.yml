# Agent instructions for ci.yml

This repository defines fleet CI policy and the planned checker. Read the policy before changing the README, checker, or a repository workflow. The checker and scheduler do not exist yet; documentation must distinguish planned behavior from implemented behavior.

## Documentation index

1. [docs/policy-general.md](docs/policy-general.md) applies to every code repository in the `zackees`, `FastLED`, and `TechWatchProject` fleet.
2. [docs/policy-rust.md](docs/policy-rust.md) adds Rust rules. Apply its library or PyPI/npm app subsection according to the artifacts the repository ships; a repository can use both profiles.
3. [docs/agent-guide.md](docs/agent-guide.md) defines how agents gather evidence, classify findings, and manage issues. Read it before auditing or filing a fleet violation.
4. [README.md](README.md) is the public summary and links to the source issues.
5. [proposal.md](proposal.md) is the consolidated CI normalization proposal, repository evidence inventory, fbuild mapping, and implementation acceptance plan. Its proposed release-cycle and event-selection rules supersede earlier design examples; enforcement is not implemented.
6. `docs/case-studies/` holds evidence-first writeups of a specific repository incident or investigation, such as [clud-ci-cost.md](docs/case-studies/clud-ci-cost.md). A case study is not policy: it becomes binding only once its candidate rules are reviewed and written into `policy-general.md` or `policy-rust.md`.

## Working rules

- Preserve required lint, test, platform, and artifact coverage when proposing a faster workflow. Confirm coverage from commands and runs, not from a runner label.
- Represent Python benchmark inputs, results, and reports internally with dataclasses, never raw dictionaries. JSON and Protocol Buffers are allowed at serialization boundaries; type any boundary dictionary with concrete value types, never `Any`. A list of records must be typed at least as `list[dict[str, TypedData]]`, with `TypedData` replaced by a declared concrete type, before validation into dataclasses.
- Treat a repo-specific deviation as either a documented exception or a finding requiring review; do not silently weaken the fleet policy.
- Use deterministic checks and GitHub run/job evidence before spending agent context on diagnosis. Label unclear expressions, scripts, and cache states as unknown.
- Do not add GitHub Actions workflow files unless the user specifically requests them.
