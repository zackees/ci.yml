"""Placeholders for commands scoped to later rounds.

Round-1A brief: "Runtime commands (units, tests size, cache save-ok/heal/
trim/janitor/budget execution, audit) are LATER rounds -- design module
boundaries so they slot in, but do not implement them now (a stub that
exits 2 with 'not implemented in round 1' is fine)."

Each stub still parses its own arguments so `ci-lint <command> --help`
works, and each names the module that will eventually own it, so a later
round has an obvious place to start:
  - units        -> ci_lint.cargo_scan (already derives targets; needs
                     size measurement, which requires an actual build)
  - tests        -> a future ci_lint.runtime_tests, driving `soldr` runs
  - cache        -> a future ci_lint.cache_runtime (save-ok/heal/trim/
                     janitor/budget execution against the Actions cache API)
  - audit        -> a future ci_lint.audit, comparing observed GitHub runs
                     against the plan (proposal.md's "ci-lint history")
"""

from __future__ import annotations

NOT_IMPLEMENTED_MESSAGE = "not implemented in round 1"


def run_stub(command: str) -> int:
    print(f"ci-lint {command}: {NOT_IMPLEMENTED_MESSAGE}")
    return 2
