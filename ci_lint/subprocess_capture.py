"""PY-003: no pipe-captured subprocess output (owner directive 2026-10-01).

Pipe capture hangs: a full pipe blocks the child, and a daemon or
grandchild that inherits the pipe keeps the parent waiting for an EOF that
never comes (see `ci_lint.proc`). Banned, per call, from the AST:

- `capture_output=True` on `subprocess.run`/`call`/`check_call`/`Popen`;
- `stdout=` / `stderr=` / `stdin=` set to `PIPE` (`subprocess.PIPE`,
  `asyncio.subprocess.PIPE`, or a bare imported `PIPE`);
- `subprocess.check_output`, `getoutput`, `getstatusoutput` (pipes by
  construction).

Use `ci_lint.proc.run_captured` (temporary files, no pipe), `stdout=` a
file you opened, `subprocess.DEVNULL`, or inherit the parent's streams.
A genuine exception (e.g. an interactive protocol over stdin/stdout that is
drained concurrently) takes a same-line `# ci-lint: allow PY-003 <reason>`
on the call's first line.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

RULE = "PY-003"
_CALLS = frozenset({"run", "call", "check_call", "Popen", "create_subprocess_exec", "create_subprocess_shell"})
_ALWAYS_PIPED = frozenset({"check_output", "getoutput", "getstatusoutput"})
_STREAM_KWARGS = ("stdout", "stderr", "stdin")
_ALLOW = re.compile(rf"ci-lint:\s*allow\s+{RULE}\s+\S")


@dataclass(frozen=True)
class CaptureViolation:
    path: str
    line: int
    call: str
    reason: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.call}(...) {self.reason}"


def _func_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_subprocess_call(node: ast.Call) -> bool:
    """`subprocess.X(...)`, `asyncio.X(...)` or a bare imported name. A
    method named `run` on some other object (`pool.run`) is not matched
    unless it uses a subprocess-only keyword."""

    func = node.func
    if isinstance(func, ast.Attribute):
        base = func.value
        root = base.attr if isinstance(base, ast.Attribute) else base.id if isinstance(base, ast.Name) else ""
        return root in ("subprocess", "asyncio", "sp")
    return isinstance(func, ast.Name)


def _is_pipe(value: ast.expr) -> bool:
    if isinstance(value, ast.Attribute):
        return value.attr == "PIPE"
    return isinstance(value, ast.Name) and value.id == "PIPE"


def scan_source(path: str, source: str) -> list[CaptureViolation]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    out: list[CaptureViolation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _func_name(node)
        reason = ""
        if name in _ALWAYS_PIPED and _is_subprocess_call(node):
            reason = "always captures through a pipe"
        elif name in _CALLS and _is_subprocess_call(node):
            for kw in node.keywords:
                if kw.arg == "capture_output" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
                    reason = "uses capture_output (pipes)"
                elif kw.arg in _STREAM_KWARGS and _is_pipe(kw.value):
                    reason = f"sets {kw.arg}=PIPE"
                if reason:
                    break
        if not reason:
            continue
        first = lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""
        if _ALLOW.search(first):
            continue
        out.append(CaptureViolation(path, node.lineno, name, reason))
    return out
