"""PY-002: records are typed dataclasses, not tuples or dictionaries.

Owner directive (2026-10-01): tuples and dictionaries used as records are
strongly discouraged in favour of typed dataclasses; `-> tuple[int,
list[str], int]` is a policy violation. A positional tuple hides what each
slot means and a `dict[str, X]` record hides which keys exist; a frozen
dataclass names both and lets the type checker hold them.

What this scan reports, per Python file, from the AST:

- a **heterogeneous tuple** annotation -- `tuple[A, B, ...]` with two or
  more element types -- on a function return or a dataclass field.
  `tuple[X, ...]` (an immutable homogeneous sequence) is fine.
- a **dict** (`dict[...]`, `Dict[...]`) annotation on a function return or
  a dataclass field, unless its value type is a declared wire-boundary
  value (`JsonValue`, `YamlValue`, `TomlValue`): JSON/YAML/TOML are allowed
  at serialization boundaries, typed concretely (AGENTS.md).

Parameters are not scanned: a function may still *accept* a mapping such
as `os.environ`. The counts are a ratchet (`ci_lint/tests/typed_records_baseline.json`):
existing code may only go down, and new files start at zero.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

WIRE_VALUE_TYPES: frozenset[str] = frozenset({"JsonValue", "YamlValue", "TomlValue"})
_TUPLE_NAMES = frozenset({"tuple", "Tuple"})
_DICT_NAMES = frozenset({"dict", "Dict"})


@dataclass(frozen=True)
class RecordViolation:
    path: str
    line: int
    where: str
    annotation: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.where} is annotated `{self.annotation}`"


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _offending(node: ast.expr | None) -> bool:
    """True when the annotation contains a record-shaped tuple or dict."""

    if node is None:
        return False
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Subscript):
            continue
        base = _name(sub.value)
        if base in _TUPLE_NAMES:
            items = sub.slice.elts if isinstance(sub.slice, ast.Tuple) else [sub.slice]
            homogeneous = len(items) == 2 and isinstance(items[1], ast.Constant) and items[1].value is Ellipsis
            if len(items) >= 2 and not homogeneous:
                return True
        elif base in _DICT_NAMES:
            items = sub.slice.elts if isinstance(sub.slice, ast.Tuple) else [sub.slice]
            value = ast.unparse(items[-1]) if items else ""
            if not any(wire in value for wire in WIRE_VALUE_TYPES):
                return True
    return False


def _is_dataclass(cls: ast.ClassDef) -> bool:
    for dec in cls.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if _name(target) == "dataclass":
            return True
    return False


def scan_source(path: str, source: str) -> list[RecordViolation]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[RecordViolation] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _offending(node.returns):
            out.append(RecordViolation(path, node.lineno, f"return of {node.name}()", ast.unparse(node.returns)))
        elif isinstance(node, ast.ClassDef) and _is_dataclass(node):
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and _offending(item.annotation):
                    target = ast.unparse(item.target)
                    out.append(
                        RecordViolation(path, item.lineno, f"field {node.name}.{target}", ast.unparse(item.annotation))
                    )
    return out


def scan_tree(root: Path, package: str) -> list[RecordViolation]:
    """Every non-test module under `root/package`."""

    out: list[RecordViolation] = []
    for file in sorted((root / package).rglob("*.py")):
        rel = file.relative_to(root).as_posix()
        if "/tests/" in rel:
            continue
        out.extend(scan_source(rel, file.read_text(encoding="utf-8")))
    return out
