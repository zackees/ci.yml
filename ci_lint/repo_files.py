"""Shared git-tracked-file enumeration for every text/AST scan.

Round-2A brief, defect 1: LAYOUT-001, the GEN-005 tracked-script scan,
TOOL-001's Python AST scan, RUST-011's `cfg(feature)` scan and PKG-005 each
walked the filesystem directly (`Path.rglob`), so running precheck against
a real checkout scanned gitignored build output too -- `.cargo/registry/**`
alone produced 1,750 bogus findings against the template repo.

`list_repo_files` is the one place this is fixed: inside a git work tree it
asks git (`git ls-files -co --exclude-standard`: cached + others, minus
anything `.gitignore`-excluded -- the same flag set `ci_lint.rules.shell`
already used for its tracked-script scan), so results match exactly what a
real CI checkout would see. A bare on-disk fixture directory that is not
itself inside any git work tree (for example a `tempfile.TemporaryDirectory()`
used by a unit test) falls back to a filesystem walk that still skips VCS
and build noise: `.git`, `target`, `.venv*`, `.cargo/registry`,
`node_modules`, `dist`, `__pycache__`.
"""

from __future__ import annotations

from pathlib import Path

from ci_lint.proc import run_captured

WALK_EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        "target",
        "node_modules",
        "dist",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
    }
)
WALK_EXCLUDED_PART_PREFIXES: tuple[str, ...] = (".venv",)
CARGO_REGISTRY_PREFIX = ".cargo/registry/"


def _is_git_work_tree(repo_root: Path) -> bool:
    try:
        proc = run_captured(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo_root, timeout=15)
    except OSError:
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _git_tracked_files(repo_root: Path) -> list[str] | None:
    """`git ls-files -co --exclude-standard`, run with `cwd=repo_root` so
    paths come back relative to `repo_root` even when it is a subdirectory
    of a larger work tree (as every fixture directory under
    `ci_lint/tests/fixtures/` is, since they are themselves tracked files of
    this repository). Returns None if git is unavailable or the command
    fails, so the caller can fall back to a filesystem walk."""

    if not _is_git_work_tree(repo_root):
        return None
    try:
        proc = run_captured(["git", "ls-files", "-co", "--exclude-standard"], cwd=repo_root, timeout=30)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return sorted({line.strip() for line in proc.stdout.splitlines() if line.strip()})


def _walk_files(repo_root: Path) -> list[str]:
    out: list[str] = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root)
        parts = rel.parts
        if any(part in WALK_EXCLUDED_DIR_NAMES for part in parts):
            continue
        if any(part.startswith(WALK_EXCLUDED_PART_PREFIXES) for part in parts):
            continue
        rel_posix = rel.as_posix()
        if rel_posix.startswith(CARGO_REGISTRY_PREFIX) or f"/{CARGO_REGISTRY_PREFIX}" in rel_posix:
            continue
        out.append(rel_posix)
    return sorted(out)


def list_repo_files(repo_root: Path) -> list[str]:
    """Repo-relative, forward-slash paths that a real CI checkout of
    `repo_root` would see: git-tracked (or not-yet-tracked-but-not-ignored)
    files when `repo_root` is inside a git work tree, otherwise a
    filesystem walk that skips VCS/build noise."""

    tracked = _git_tracked_files(repo_root)
    if tracked is not None:
        return tracked
    return _walk_files(repo_root)
