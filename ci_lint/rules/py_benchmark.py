"""Group 14: PY-001 -- benchmark code uses raw dicts / `Any` instead of

dataclasses (M2-21, ci.yml#44 part B). AGENTS.md's own working rule ("represent
Python benchmark inputs, results, and reports internally with dataclasses,
never raw dictionaries ... type any boundary dictionary with concrete value
types, never `Any`") applies to any repository, but until now no scanner
checked a target repository against it.

Scope: the Python source file(s) named by `[suites.perf].run` (a
`python3 <path> ...` / `uv run ... <path>` / bare `<path>` command), plus
any local `ci/*.py` script one `subprocess`/argv level deep from it (the
same one-level-deep convention GEN-004 uses). Only these declared
perf/benchmark files are scanned -- this is not a repo-wide Python audit.

An AST walk over each scanned file flags:
- `from typing import Any` / `typing.Any`, anywhere.
- A function whose name contains "result", "record", "benchmark", or
  "bench" and whose return annotation is a bare `dict`/`Dict` or a
  `dict[...]`/`Dict[...]` subscript (the boundary-typing rule requires a
  declared dataclass, not a dict alias, once the value crosses out of pure
  JSON/protobuf serialization).
"""

from __future__ import annotations

import ast
import shlex
from pathlib import Path

from ci_lint.finding import Finding
from ci_lint.schema import CiToml

NAME_HINTS = ("result", "record", "benchmark", "bench")


def _perf_entry_points(ci: CiToml) -> list[str]:
    perf = ci.suites.get("perf")
    if perf is None:
        return []
    try:
        tokens = shlex.split(perf.run)
    except ValueError:
        return []
    return [t for t in tokens if t.endswith(".py")]


def _collect_ci_py_imports(repo_root: Path, entry_rel: str) -> set[str]:
    """One level deep: `ci/*.py` files referenced as argv/subprocess targets
    inside the entry point itself (GEN-004's own convention, reused)."""
    found: set[str] = set()
    path = repo_root / entry_rel
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return found
    for line in text.splitlines():
        for tok in line.replace("'", '"').split('"'):
            if tok.startswith("ci/") and tok.endswith(".py"):
                found.add(tok)
    return found


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.findings: list[Finding] = []

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.module == "typing":
            for alias in node.names:
                if alias.name == "Any":
                    self.findings.append(
                        Finding(
                            rule="PY-001",
                            path=self.path,
                            line=node.lineno,
                            message="imports 'typing.Any' in benchmark code",
                            fix="replace the Any-typed value with a declared frozen dataclass "
                            "field, or a concrete Union of the types actually produced",
                        )
                    )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr == "Any" and isinstance(node.value, ast.Name) and node.value.id == "typing":
            self.findings.append(
                Finding(
                    rule="PY-001",
                    path=self.path,
                    line=node.lineno,
                    message="references 'typing.Any' in benchmark code",
                    fix="replace the Any-typed value with a declared frozen dataclass field, "
                    "or a concrete Union of the types actually produced",
                )
            )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._check_return(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._check_return(node)
        self.generic_visit(node)

    def _check_return(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        name_lower = node.name.lower()
        if not any(hint in name_lower for hint in NAME_HINTS):
            return
        ann = node.returns
        if ann is None:
            return
        is_dict = (isinstance(ann, ast.Name) and ann.id in ("dict", "Dict")) or (
            isinstance(ann, ast.Subscript)
            and isinstance(ann.value, (ast.Name, ast.Attribute))
            and (
                (isinstance(ann.value, ast.Name) and ann.value.id in ("dict", "Dict"))
                or (isinstance(ann.value, ast.Attribute) and ann.value.attr in ("dict", "Dict"))
            )
        )
        if is_dict:
            self.findings.append(
                Finding(
                    rule="PY-001",
                    path=self.path,
                    line=node.lineno,
                    message=f"'{node.name}' returns a raw dict instead of a dataclass",
                    fix=f"declare a frozen dataclass for '{node.name}''s return value and "
                    "construct/validate it there; keep dict/JSON only at the serialization "
                    "boundary",
                )
            )


def check_group14(ci: CiToml, repo_root: Path) -> list[Finding]:
    entry_points = _perf_entry_points(ci)
    if not entry_points:
        return []

    scan_paths: set[str] = set(entry_points)
    for entry in list(entry_points):
        scan_paths |= _collect_ci_py_imports(repo_root, entry)

    findings: list[Finding] = []
    for rel in sorted(scan_paths):
        full = repo_root / rel
        try:
            text = full.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError:
            continue
        visitor = _Visitor(rel)
        visitor.visit(tree)
        findings.extend(visitor.findings)
    return findings
