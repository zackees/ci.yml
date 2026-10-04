"""Base-revision Python import closure for GATE-008 gate entry points.

Resolve only tracked modules, without importing or executing repository code.
Function-local and conditional imports are deliberately over-approximated.
Dynamic imports still require explicit trust surfaces. Symlinked modules
force remote execution for every change rather than trusting unresolved targets.
"""

from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath


def _module_paths(base: PurePosixPath, name: str, tracked: set[str]) -> set[str]:
    parts = (*base.parts, *filter(None, name.split(".")))
    module = PurePosixPath(*parts)
    candidates = {f"{module}.py", str(module / "__init__.py")}
    candidates.update(
        str(PurePosixPath(*parts[:index]) / "__init__.py")
        for index in range(1, len(parts))
    )
    # A tracked bare prefix can be a symlinked package directory.
    candidates.update(str(PurePosixPath(*parts[:index])) for index in range(1, len(parts) + 1))
    return candidates & tracked


def _from_paths(
    node: ast.ImportFrom, source: str, tracked: set[str],
    search_roots: tuple[PurePosixPath, ...],
) -> set[str]:
    parent = PurePosixPath(source).parent
    if node.level:
        if node.level > len(parent.parts):
            return set()
        bases = (PurePosixPath(*parent.parts[:len(parent.parts) - node.level + 1]),)
    else:
        bases = (parent, PurePosixPath(), *search_roots)
    names = [node.module or ""]
    names.extend(
        ".".join(filter(None, (node.module, alias.name)))
        for alias in node.names if alias.name != "*"
    )
    return {path for base in bases for name in names
            for path in _module_paths(base, name, tracked)}


def _imports(
    text: str, source: str, tracked: set[str],
    search_roots: tuple[PurePosixPath, ...] = (),
) -> set[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # Unknown import structure cannot authorize a narrower surface.
        return {str(PurePosixPath(source).parent / "**")}
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            result.update(_from_paths(node, source, tracked, search_roots))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                for base in (PurePosixPath(source).parent, PurePosixPath(), *search_roots):
                    result.update(_module_paths(base, alias.name, tracked))
    return result


def python_surfaces(
    repo: Path, rev: str, tracked: set[str], roots: tuple[str, ...],
) -> tuple[str, ...]:
    """Return entry points and their statically resolvable local imports."""
    from ci_lint.local_gate import GitError, _git  # noqa: PLC0415

    pending = [path for path in roots if path.endswith(".py") and path in tracked]
    search_roots = tuple(PurePosixPath(path).parent for path in pending)
    seen: set[str] = set()
    surfaces: set[str] = set(pending)
    while pending:
        source = pending.pop()
        if source in seen:
            continue
        seen.add(source)
        try:
            mode = _git(repo, "ls-tree", "--format=%(objectmode)", rev, "--", source).strip()
            if mode == "120000":
                # Parsing a symlink's target string as Python would silently
                # omit its actual dependencies. Keep every change remote.
                surfaces.add("**")
                continue
            text = _git(repo, "show", f"{rev}:{source}")
        except GitError:
            surfaces.add(str(PurePosixPath(source).parent / "**"))
            continue
        imported = _imports(text, source, tracked, search_roots)
        surfaces.update(imported)
        pending.extend(path for path in imported if path in tracked and path not in seen)
    return tuple(sorted(surfaces))
