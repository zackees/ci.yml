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

The same check covers **parameters and annotated variables** (owner
directive 2026-10-02: "dataclass for complex types, no dictionaries"):
`jobs: dict[str, dict[str, dict[str, YamlValue]]]` is a record and must be
a dataclass. The one exception there is a *flat string map* --
a dict whose values are scalars (`dict[str, str]`, `Mapping[str, int]`,
`dict[str, Path | None]`) -- so a function may still accept an environment,
a header map such as `os.environ`, or a counter. A dict whose values are
containers, records, `object`/`Any` or wire values (`dict[str, list[...]]`,
`dict[str, dict[...]]`, `dict[str, JsonValue]`) is a record and is reported. `Mapping`,
`MutableMapping`, `defaultdict` and `OrderedDict` count as dicts.

The counts are a ratchet (`ci_lint/tests/typed_records_baseline.json`):
existing code may only go down, and new files start at zero.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

WIRE_VALUE_TYPES: frozenset[str] = frozenset({"JsonValue", "YamlValue", "TomlValue"})
_TUPLE_NAMES = frozenset({"tuple", "Tuple"})
_DICT_NAMES = frozenset({"dict", "Dict", "Mapping", "MutableMapping", "defaultdict", "OrderedDict", "DefaultDict"})


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


_UNTYPED = frozenset({"object", "Any"})


def _complex_map(sub: ast.Subscript) -> bool:
    """For parameters and variables: a dict is a record when its values are
    themselves maps (`dict[str, dict[str, YamlValue]]`) or untyped
    (`object`, `Any`). A scalar-valued map (environment, counter), an index
    of dataclasses (`dict[str, Entry]`, `dict[str, list[Entry]]`) and a
    single-level parsed document at its boundary (`dict[str, TomlValue]`)
    are not."""

    items = sub.slice.elts if isinstance(sub.slice, ast.Tuple) else [sub.slice]
    if not items:
        return True
    for node in ast.walk(items[-1]):
        if isinstance(node, ast.Subscript) and _name(node.value) in _DICT_NAMES:
            return True
        if isinstance(node, (ast.Name, ast.Attribute)) and _name(node) in _UNTYPED:
            return True
    return False


def _offending(node: ast.expr | None, *, lenient: bool = False) -> bool:
    """True when the annotation contains a record-shaped tuple or dict.
    `lenient` (parameters, variables) applies `_complex_map` to dicts
    instead of the return/field rule: only nested or untyped maps."""

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
            if lenient:
                if _complex_map(sub):
                    return True
                continue
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
    fields: set[int] = set()  # dataclass fields, already judged by the stricter field rule
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _offending(node.returns):
                out.append(RecordViolation(path, node.lineno, f"return of {node.name}()", ast.unparse(node.returns)))
            a = node.args
            for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs, *(x for x in (a.vararg, a.kwarg) if x)):
                if _offending(arg.annotation, lenient=True):
                    assert arg.annotation is not None
                    out.append(RecordViolation(path, arg.lineno, f"parameter {node.name}({arg.arg})",
                                               ast.unparse(arg.annotation)))
        elif isinstance(node, ast.ClassDef) and _is_dataclass(node):
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and _offending(item.annotation):
                    fields.add(id(item))
                    target = ast.unparse(item.target)
                    out.append(
                        RecordViolation(path, item.lineno, f"field {node.name}.{target}", ast.unparse(item.annotation))
                    )
    for node in ast.walk(tree):
        if (isinstance(node, ast.AnnAssign) and id(node) not in fields
                and _offending(node.annotation, lenient=True)):
            out.append(RecordViolation(path, node.lineno, f"variable {ast.unparse(node.target)}",
                                       ast.unparse(node.annotation)))
    return sorted(out, key=lambda v: v.line)


def scan_tree(root: Path, package: str) -> list[RecordViolation]:
    """Every non-test module under `root/package`."""

    out: list[RecordViolation] = []
    for file in sorted((root / package).rglob("*.py")):
        rel = file.relative_to(root).as_posix()
        if "/tests/" in rel:
            continue
        out.extend(scan_source(rel, file.read_text(encoding="utf-8")))
    return out
