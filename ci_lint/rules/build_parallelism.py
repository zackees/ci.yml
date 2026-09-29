"""GEN-012: build parallelism must not be pinned.

zackees/ci.yml#30: `CARGO_BUILD_JOBS` / `SOLDR_JOBS` set anywhere in a
workflow or composite action (workflow-, job-, or step-level `env:`, or
exported/assigned in a `run:` line), and `-j`/`--jobs` passed on a
`cargo`/`soldr` command line, pin build parallelism to a fixed core count.
That is wrong twice over: it leaves a multi-core runner running single- (or
N-)threaded regardless of runner size, and a literal number goes stale the
moment runner sizing changes. Left unset, cargo and soldr already default to
every available core (evidence: zackees/zccache#1800, where a `cargo test
--no-run` compile step pinned to 1 job dominated the slowest PR job's
median, 7.3 min).

This is a pure text scan (workflow/composite-action YAML is treated as
lines, not parsed) so a same-line trailing `# ci-lint: allow GEN-012
<reason>` comment -- the escape hatch this issue asks for, e.g. a
timing-sensitive test step that must legitimately serialize -- can be
matched against the exact line it excuses, and so a comment that merely
*mentions* one of the variable names (not a setting) never matches: it
never appears left of the `#` that starts a comment.
"""

from __future__ import annotations

import re
from pathlib import Path

from ci_lint.finding import Finding
from ci_lint.workflow_scan import discover_composite_actions, discover_workflow_files

PINNED_VARS: frozenset[str] = frozenset({"CARGO_BUILD_JOBS", "SOLDR_JOBS"})

# A YAML mapping entry setting one of the pinned vars, e.g.:
#   CARGO_BUILD_JOBS: "1"
#   - SOLDR_JOBS: 4          (rare, but tolerate a leading "- ")
_ENV_KEY_RE = re.compile(
    r'^\s*(?:-\s+)?["\']?(CARGO_BUILD_JOBS|SOLDR_JOBS)["\']?\s*:\s*\S'
)

# A shell-style assignment inside a `run:` block, e.g.:
#   export CARGO_BUILD_JOBS=1
#   SOLDR_JOBS=4 soldr cargo build
_EXPORT_RE = re.compile(r"\b(?:export\s+)?(CARGO_BUILD_JOBS|SOLDR_JOBS)\s*=\s*\S+")

# `-j`/`--jobs` on a cargo/soldr command line, anywhere left of the comment
# marker on the line: `cargo build -j 2`, `soldr cargo test -j2`,
# `cargo build --jobs=4`.
_JOBS_FLAG_RE = re.compile(
    r"\b(cargo|soldr)\b[^\n]*\s(-j\s*\d+|-j\d+|--jobs[= ]\d+)"
)

_ALLOW_RE = re.compile(r"ci-lint:\s*allow\s+GEN-012\s+\S")


def _rel(repo_root: Path, path: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _scan_file(repo_root: Path, path: Path) -> list[Finding]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    rel = _rel(repo_root, path)
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        code_part, _, comment_part = line.partition("#")
        if not code_part.strip():
            continue  # whole line is a comment -- mentioning a var name here is fine

        detail: str | None = None
        m = _ENV_KEY_RE.match(code_part)
        if m:
            detail = f"sets '{m.group(1)}' in env"
        else:
            m2 = _EXPORT_RE.search(code_part)
            if m2:
                detail = f"assigns '{m2.group(1)}' in a run: step"

        m3 = _JOBS_FLAG_RE.search(code_part)
        if m3 and detail is None:
            detail = f"passes '{m3.group(2)}' to a {m3.group(1)} command"

        if detail is None:
            continue

        if _ALLOW_RE.search(comment_part):
            continue  # explicit, reasoned exception on this exact line

        findings.append(
            Finding(
                rule="GEN-012",
                path=rel,
                line=lineno,
                message=f"{rel}:{lineno} {detail}, pinning build parallelism to a fixed core count",
                fix="remove the pinned setting/flag and let cargo/soldr default to every available "
                "core; if this exact step must legitimately serialize (e.g. timing-sensitive tests), "
                "add a trailing '# ci-lint: allow GEN-012 <reason>' comment on this same line instead "
                "of pinning a number that will go stale when runner sizing changes",
            )
        )
    return findings


def check_gen_012(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in discover_workflow_files(repo_root):
        findings.extend(_scan_file(repo_root, path))
    for path in discover_composite_actions(repo_root):
        findings.extend(_scan_file(repo_root, path))
    return findings
