"""GEN-013: no copy-over-restored-cache-tree step after a Dylint/target
cache restore.

zackees/ci.yml#49 (#1's read-only-tree acceptance bullet): the first live
Dylint investigations hit `Permission denied` when a workflow step copied a
host-qualified output tree back over an already-restored short-name tree
(extension2 run 36347078670, attempt 2 -- see #1's body). setup-soldr#539
(ingested here as v0.9.81) fixed the ROOT CAUSE of that one instance by
building `dylintOutputPaths` from the host-qualified toolchain directly, so
setup-soldr's own restore/save no longer needs an alias-copy workaround
(`src/lib/resolve-setup.ts`: `dylintOutputPaths` are used as-is by
`actions/cache`'s restore/save, and the only `fs.copyFileSync`/`fs.cpSync`
call sites left in setup-soldr's own source, per `git grep -n
'cpSync\\|copyFileSync\\|shutil\\|copytree'` on
zackees/setup-soldr@869aa7b, are single-file binary copies in
`ensure-soldr.ts`/`ensure-rust-toolchain.ts` and the isolated-build-cache
seeder in `seed-isolated-cache.ts`, none of which touch a Dylint/target
output path).

That fix does not make the general failure mode impossible: any repository's
OWN workflow or `ci/*.py` script can still write a `cp -r`/`rsync`/
`shutil.copytree` step that copies a directory tree into a `target/` or
Dylint output path (`target/dylint/...`) after that same job already
restored one there (an `actions/cache*` step, directly or through the
sanctioned wrapper). Restored files can be read-only, so that copy is a
`Permission denied` waiting to happen the next time cache identities drift
-- exactly the shape #1 documented. This rule is a pure text/line scan (no
YAML parse needed for the copy detection itself) across workflow YAML,
composite action YAML, and tracked `*.py` scripts, so it also catches a
one-line `run:` step delegating to a Python script that does the copy.
"""

from __future__ import annotations

import re
from pathlib import Path

from ci_lint.finding import Finding
from ci_lint.repo_files import list_repo_files
from ci_lint.workflow_scan import discover_composite_actions, discover_workflow_files

# A cache restore surface: the raw action, its /restore sub-action, or the
# fleet's sanctioned wrapper directory (ci_lint.rules.cache_static
# CACHE-001), which itself calls actions/cache under the hood.
_CACHE_RESTORE_RE = re.compile(
    r"uses:\s*\S*actions/cache(?:/restore)?@|uses:\s*\./\.github/actions/cache\b"
)

# cp -r/-R (any flag order, e.g. `cp -a`, `cp -rf`), rsync, or
# shutil.copytree(...) -- a directory-tree copy, not a single-file copy.
_TREE_COPY_RE = re.compile(
    r"\bcp\s+(?:-\w*[raR]\w*\s+|--recursive\s+|--archive\s+)+\S|"
    r"\brsync\b|"
    r"shutil\.copytree\("
)

# Destination looks like a restored build/Dylint output tree.
_TARGET_DEST_RE = re.compile(r"target/|dylint", re.IGNORECASE)

_ALLOW_RE = re.compile(r"ci-lint:\s*allow\s+GEN-013\s+\S")


def _rel(repo_root: Path, path: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _scan_file(repo_root: Path, path: Path, *, require_restore_precedent: bool) -> list[Finding]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    rel = _rel(repo_root, path)
    findings: list[Finding] = []
    # A `.py` script can't see its own caller's `actions/cache*` step (that
    # lives in the workflow YAML that invokes it, per the one-line `run:`
    # convention), so it is scoped by destination shape alone rather than by
    # an in-file restore precedent.
    saw_cache_restore = not require_restore_precedent
    for lineno, line in enumerate(text.splitlines(), start=1):
        code_part, _, comment_part = line.partition("#")
        if require_restore_precedent and _CACHE_RESTORE_RE.search(code_part):
            saw_cache_restore = True
            continue
        if not saw_cache_restore or not code_part.strip():
            continue
        if _TREE_COPY_RE.search(code_part) and _TARGET_DEST_RE.search(code_part):
            if _ALLOW_RE.search(comment_part):
                continue
            findings.append(
                Finding(
                    rule="GEN-013",
                    path=rel,
                    line=lineno,
                    message=f"{rel}:{lineno} copies a directory tree onto a target/Dylint output "
                    "path after this job already restored a cache tree there -- a restored tree "
                    "can be read-only, so this copy can fail with 'Permission denied' the moment "
                    "cache identities drift (zackees/ci.yml#1, extension2 run 36347078670)",
                    fix="do not copy over a restored cache tree; restore directly to the path the "
                    "job will use (fix the cache key/output-path identity instead of aliasing two "
                    "trees), or if this exact copy is required and provably safe, add a trailing "
                    "'# ci-lint: allow GEN-013 <reason>' comment on this same line",
                )
            )
    return findings


def check_gen_013(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in discover_workflow_files(repo_root):
        findings.extend(_scan_file(repo_root, path, require_restore_precedent=True))
    for path in discover_composite_actions(repo_root):
        findings.extend(_scan_file(repo_root, path, require_restore_precedent=True))
    for rel in list_repo_files(repo_root):
        if rel.endswith(".py"):
            findings.extend(
                _scan_file(repo_root, repo_root / rel, require_restore_precedent=False)
            )
    return findings
