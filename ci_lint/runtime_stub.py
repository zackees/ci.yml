"""Placeholders for commands scoped to later rounds.

Round-1A implemented `precheck` and `plan`. Round-2A (this round) added the
runtime commands that need a real build/artifact/venv: `units`, `tests
size`, `wheel check`/`wheel installed`, `gate`, `precheck --local` and
`selftest` -- see `ci_lint.runtime.*`, `ci_lint.cargo_messages` and
`ci_lint.selftest`.

Still stubbed, pending round 4 ("Cache"):
  - cache -> a future ci_lint.runtime.cache (save-ok/heal/trim/janitor/
             budget execution against the GitHub Actions cache API; also
             owns the CACHE-005/006/008/ACT-001 live checks that
             `precheck --local` currently reports as explicitly skipped)
  - audit -> a future ci_lint.audit, comparing observed GitHub runs
             against the plan (proposal.md's "ci-lint history")

Each stub still parses its own arguments so `ci-lint <command> --help`
works.
"""

from __future__ import annotations

NOT_IMPLEMENTED_MESSAGE = "not implemented yet"


def run_stub(command: str) -> int:
    print(f"ci-lint {command}: {NOT_IMPLEMENTED_MESSAGE}")
    return 2
