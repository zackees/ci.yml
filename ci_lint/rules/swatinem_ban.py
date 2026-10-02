"""CACHE-025: `Swatinem/rust-cache` is banned -- Rust build caching goes
through `zackees/setup-soldr` / soldr (zackees/ci.yml#209).

Swatinem caches `~/.cargo` and whole `target/` trees under hand-chosen
keys (`shared-key: rust-workspace-v1`, invalidated by bumping the suffix
by hand). The fleet's Rust build cache is soldr's: setup-soldr's
zccache-backed build cache (content/lockfile/toolchain-keyed, nearest-
ancestor restore -- candidate CACHE-024, #185) and the attestation lineage
keys of GATE-010. A Swatinem step duplicates that cache, invalidates it ad
hoc, spends the repository's 10 GiB Actions cache budget on `target/`
trees `[cache].never` already forbids, and cannot be reproduced by a
bosn -> act local run.

Static signal, a plain line scan (no YAML tooling needed, so it never
degrades to needs_review): a `uses: Swatinem/rust-cache@<ref>` step -- any
ref, any letter case, quoted or not, `- uses:` or `uses:` -- in a workflow
(`.github/workflows/*.yml|yaml`) or composite action is a violation. A
commented-out line never matches.

No exceptions (maintainer decision 2026-10-02): a same-line
`# ci-lint: allow CACHE-025 <reason>` does not excuse the step, and
`ci_lint.exceptions.apply_exceptions` refuses a ci.toml `[[exceptions]]`
entry for this rule (UNWAIVABLE_RULES). That covers the former carve-outs
too -- a soldr bootstrap job and a benchmark using Swatinem as its
comparison baseline both fail.
"""

from __future__ import annotations

import re
from pathlib import Path

from ci_lint.finding import Finding
from ci_lint.workflow_scan import discover_composite_actions, discover_workflow_files

RULE = "CACHE-025"

_USES_SWATINEM = re.compile(r"""^\s*(?:-\s+)?uses:\s*["']?swatinem/rust-cache(?:@|["'\s]|$)""", re.IGNORECASE)

FIX = (
    "delete the Swatinem/rust-cache step: Rust build caching goes through zackees/setup-soldr@v0 "
    "(its zccache-backed build cache, on by default -- no `cache: false`, no ZCCACHE_DISABLE) or "
    "soldr itself (`soldr cargo ...`, `soldr cook`); a job not on soldr yet migrates to setup-soldr "
    "+ `soldr cargo`. There are no exceptions -- not a soldr bootstrap job, not a benchmark baseline "
    "(maintainer decision 2026-10-02; docs/policy-rust.md, zackees/ci.yml#209)"
)


def scan_text(text: str, rel_path: str) -> list[Finding]:
    findings: list[Finding] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not _USES_SWATINEM.match(raw):
            continue
        findings.append(
            Finding(
                rule=RULE,
                path=rel_path,
                line=lineno,
                message=f"`{raw.strip().removeprefix('- ').strip()}` caches ~/.cargo and target/ under a "
                "hand-chosen key, duplicating setup-soldr's build cache",
                fix=FIX,
            )
        )
    return findings


def check_cache_025(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in [*discover_workflow_files(repo_root), *discover_composite_actions(repo_root)]:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(scan_text(text, path.relative_to(repo_root).as_posix()))
    return findings
