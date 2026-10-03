"""GHAPI-001 (static): machine-local CI tooling re-reads GitHub state in a
loop without a time or cursor bound (zackees/ci.yml#224).

The policy (docs/policy-general.md, "GitHub API budget") routes every read
of PR/run/job/check/comment state through one per-machine broker that keeps
a durable, time-indexed cache, so a re-query asks only for what changed
after the cache's high-water mark. The broker is not shipped yet
(docs/designs/ghapi-broker.md, zackees/clud#1743); this is the static signal
for the shape that burned the 5,000/h REST budget twice in one FastLED
session: a poller that re-fetches a run's full job list, a PR's full check
set or a whole comment thread every few seconds.

Where it looks:

- every workflow / composite-action `run:` block (shell);
- every script under `ci/` (`.py` by AST, `.sh`/`.bash` as shell) --
  GATE-012's convention, a superset of GEN-004's one-level follow of the
  `ci/*.py` scripts a `run:` line names;
- every `.py`/`.sh`/`.bash` file under a `skills/` or `tools/` directory, or
  under `.claude/` -- agent skills, watchers and landers. ci_lint had no
  prior convention for these; GHAPI-001 introduces this one because the
  policy names them explicitly. Markdown skill bodies are not parsed.

What it reports:

- **violation**: a GitHub read -- `gh api` (GET), `gh pr checks`, `gh pr
  view`, `gh run view`, `gh run list`, or curl/wget/requests/urllib against
  `api.github.com` -- reachable from a `while`/`until` loop that sleeps,
  with no bound on the query. In Python the read may sit in a helper the
  loop calls (same-module call graph, including a `gh(*args)` wrapper that
  builds `["gh", *args]`).
- **violation**: `gh run watch`, `gh pr checks --watch`, or `watch ... gh
  ...` anywhere: gh's own loop re-reads the full run or check set.
- **pass**: every read the loop reaches carries a bound -- `since=`,
  `created>=`/`updated>=` filters (`--created`), or an
  `If-None-Match`/`If-Modified-Since`/ETag conditional request.
- **needs_review**: a `for` loop that sleeps (a bounded retry or a counted
  poll), or a sleeping `while` loop whose only reads go through a
  non-literal command.

A same-line `# ci-lint: allow GHAPI-001 <reason>` on the loop's opening
line or on the read excuses it. A loop whose own text names a GitHub App
check is left to GATE-012 (no wait on app checks) in the files GATE-012
scans, so one wait is never reported twice.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ci_lint.finding import Finding, Status
from ci_lint.remote_only import WAIT_CHECK_NAMES
from ci_lint.repo_files import list_repo_files
from ci_lint.rules.test_invocations import allowed, raw_lines_of, with_yaml_comment
from ci_lint.workflow_scan import (
    as_dict,
    as_list,
    jobs_of,
    load_composite_actions,
    load_workflows,
    steps_of,
)
from ci_lint.yaml_io import LoadStatus


RULE = "GHAPI-001"
POLICY = "docs/policy-general.md#github-api-budget-one-controlled-query-mechanism-per-machine-ghapi-001"
SHELL_SUFFIXES: frozenset[str] = frozenset({".sh", ".bash"})
TOOL_DIR_NAMES: frozenset[str] = frozenset({"skills", "tools"})

_FIX = (
    "read through the per-machine GitHub broker (docs/designs/ghapi-broker.md) instead of polling; "
    "until it ships, bound every re-query by what you already hold -- `since=` on comment listings, "
    "`created>=`/`--created` on run listings, or a conditional request with `If-None-Match: <etag>` "
    "(a 304 costs no rate limit) -- and stop re-reading terminal runs/jobs/PRs. A deliberate "
    f"exception takes a same-line `# ci-lint: allow {RULE} <reason>` ({POLICY})"
)

_BOUND = re.compile(
    r"[?&]?since=|created[:=]>|updated[:=]>|if-none-match|if-modified-since|\betag\b",
    re.IGNORECASE,
)
_BOUND_TOKENS: frozenset[str] = frozenset(
    {"since", "--created", "if-none-match", "if-modified-since", "etag"}
)

# ── shell ──────────────────────────────────────────────────────────────────

_SH_READ = re.compile(
    r"\bgh\s+(?:api\b|pr\s+(?:checks|view)\b|run\s+(?:view|list)\b)"
    r"|\b(?:curl|wget)\b.*api\.github\.com"
)
_SH_WRITE = re.compile(
    r"\bgh\s+api\b.*(?:-X|--method)\s*['\"]?(?:POST|PATCH|PUT|DELETE)", re.IGNORECASE
)
_SH_WATCH = re.compile(
    r"\bgh\s+run\s+watch\b|\bgh\s+pr\s+checks\b[^;&|]*--watch\b|\bwatch\b[^;&|]*\bgh\s+"
)
_SH_SLEEP = re.compile(r"(?:^|[\s;&|(])sleep\b")
_SH_OPEN = re.compile(r"(?:^|[;&|({]|\bdo\b|\bthen\b|\belse\b)\s*(while|until|for)\b")
_SH_DONE = re.compile(r"(?:^|[;&|])\s*done\b")


@dataclass(frozen=True)
class ShellLoop:
    kind: str  # "while" / "until" / "for"
    start: int  # 0-based line index of the opener
    end: int  # 0-based line index of the matching `done`


def _strip_sh_comment(line: str) -> str:
    m = re.search(r"(^|\s)#", line)
    return line[: m.start()] if m else line


def shell_loops(lines: list[str]) -> list[ShellLoop]:
    """`while`/`until`/`for ... do ... done` spans, matched with a stack.
    A text heuristic, not a shell parser: it only needs loop extents."""

    events: list[ShellLoop] = []
    stack: list[ShellLoop] = []
    for idx, raw in enumerate(lines):
        code = _strip_sh_comment(raw)
        marks = [(m.start(1), m.group(1)) for m in _SH_OPEN.finditer(code)]
        marks += [(m.end() - 4, "done") for m in _SH_DONE.finditer(code)]
        for _pos, word in sorted(marks):
            if word == "done":
                if stack:
                    opener = stack.pop()
                    events.append(ShellLoop(opener.kind, opener.start, idx))
            else:
                stack.append(ShellLoop(word, idx, idx))
    return sorted(events, key=lambda loop: (loop.start, -loop.end))


def _names_app_check(text: str) -> bool:
    low = text.lower()
    return any(name in low for name in WAIT_CHECK_NAMES)


@dataclass(frozen=True)
class ShellSource:
    text: str
    path: str
    where: str  # "jobs.<id>.steps[i]" / "runs.steps[i]" / "script"
    raw_lines: tuple[str, ...]  # the file's raw lines, for YAML-dropped allow comments
    gate_012_scope: bool


def _line_ref(src: ShellSource, idx: int) -> int | None:
    return idx + 1 if src.where == "script" else None


def _where(src: ShellSource, idx: int) -> str:
    return (
        f"line {idx + 1}"
        if src.where == "script"
        else f"{src.where} (run line {idx + 1})"
    )


def check_shell(src: ShellSource) -> list[Finding]:
    lines = src.text.splitlines()
    raw = list(src.raw_lines)

    def is_allowed(idx: int) -> bool:
        return allowed(with_yaml_comment(lines[idx], raw), RULE)

    findings: list[Finding] = []
    reported: set[int] = set()
    for idx, line in enumerate(lines):
        code = _strip_sh_comment(line)
        if _SH_WATCH.search(code) and not is_allowed(idx):
            if src.gate_012_scope and _names_app_check(code):
                continue
            reported.add(idx)
            findings.append(
                Finding(
                    rule=RULE,
                    path=src.path,
                    line=_line_ref(src, idx),
                    message=f"{_where(src, idx)}: `{code.strip()[:100]}` is gh's own polling loop -- it "
                    "re-reads the whole run / check set every few seconds with no bound",
                    fix=_FIX,
                )
            )
    for loop in shell_loops(lines):
        body = lines[loop.start : loop.end + 1]
        code_body = [_strip_sh_comment(ln) for ln in body]
        if not any(_SH_SLEEP.search(ln) for ln in code_body):
            continue
        reads = [
            loop.start + i
            for i, ln in enumerate(code_body)
            if _SH_READ.search(ln) and not _SH_WRITE.search(ln)
        ]
        reads = [r for r in reads if r not in reported and not is_allowed(r)]
        if not reads or is_allowed(loop.start):
            continue
        joined = "\n".join(code_body)
        if _BOUND.search(joined):
            continue
        if src.gate_012_scope and _names_app_check(joined):
            continue  # GATE-012 owns a wait on an app check
        reported.update(reads)
        what = "; ".join(f"`{lines[r].strip()[:80]}`" for r in reads[:3])
        polling = loop.kind in ("while", "until")
        findings.append(
            Finding(
                rule=RULE,
                path=src.path,
                line=_line_ref(src, loop.start),
                status=Status.VIOLATION if polling else Status.NEEDS_REVIEW,
                message=f"{_where(src, loop.start)}: a sleeping `{loop.kind}` loop re-reads GitHub state "
                f"with no time/cursor bound: {what}"
                + (
                    ""
                    if polling
                    else " (a counted loop: a retry or a poll -- cannot tell statically)"
                ),
                fix=_FIX,
            )
        )
    return findings


# ── python ─────────────────────────────────────────────────────────────────

_GH_READ_ARGV: tuple[tuple[str, ...], ...] = (
    ("api",),
    ("pr", "checks"),
    ("pr", "view"),
    ("run", "view"),
    ("run", "list"),
)
_HTTP_FUNCS: frozenset[str] = frozenset({"urlopen", "Request"})
_HTTP_RECEIVERS: frozenset[str] = frozenset({"requests", "httpx"})
_SLEEP_NAMES: frozenset[str] = frozenset({"sleep"})


@dataclass(frozen=True)
class PyRead:
    line: int
    what: str  # "gh api", "gh pr checks", "api.github.com"
    bounded: bool
    literal: bool  # False: the command is not a literal argv (ambiguous)


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _receiver(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        node = func.value
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
    return ""


def _strings(node: ast.AST) -> list[str]:
    return [
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def _bounded(strings: list[str]) -> bool:
    return any(_BOUND.search(s) or s.strip().lower() in _BOUND_TOKENS for s in strings)


def _const(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _gh_read_kind(argv: list[str]) -> str | None:
    """`argv` excludes the leading `gh`. None when not a GitHub read."""

    for sub in _GH_READ_ARGV:
        if tuple(argv[: len(sub)]) != sub:
            continue
        if sub == ("api",):
            endpoint = argv[1] if len(argv) > 1 else ""
            for i, tok in enumerate(argv):
                if (
                    tok in ("-X", "--method")
                    and i + 1 < len(argv)
                    and argv[i + 1].upper() != "GET"
                ):
                    return None
                if (
                    tok.startswith(("-X", "--method="))
                    and tok not in ("-X", "--method")
                    and "GET" not in tok.upper()
                ):
                    return None
            has_fields = any(t in ("-f", "-F", "--field", "--raw-field") for t in argv)
            explicit_get = any(
                t.upper() in ("GET", "--METHOD=GET", "-XGET") for t in argv
            )
            if has_fields and endpoint != "graphql" and not explicit_get:
                return None  # gh api defaults to POST once fields are given
            if any("mutation" in t for t in argv):
                return None
        return "gh " + " ".join(sub)
    return None


def _is_watch(argv: list[str]) -> bool:
    return argv[:2] == ["run", "watch"] or (
        argv[:2] == ["pr", "checks"] and "--watch" in argv
    )


@dataclass(frozen=True)
class _Fn:
    name: str
    node: ast.FunctionDef | ast.AsyncFunctionDef


class PyModule:
    """Per-module facts: functions by name, gh wrappers, sleepers, reads."""

    def __init__(self, tree: ast.Module, source: str) -> None:
        self.tree = tree
        self.lines = source.splitlines()
        self.mentions_api = "api.github.com" in source
        self.fns: list[_Fn] = [
            _Fn(n.name, n)
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        self.fn_names: frozenset[str] = frozenset(f.name for f in self.fns)
        self.wrappers = self._fixpoint(self._direct_wrapper, self._passes_to_wrapper)
        self.sleepers = self._fixpoint(self._direct_sleeper, self._calls_sleeper)
        self._reach: dict[str, tuple[PyRead, ...]] = {}

    def _fixpoint(
        self, direct: Callable[[ast.AST], bool], transitive: Callable[[ast.AST, set[str]], bool]
    ) -> frozenset[str]:
        found = {f.name for f in self.fns if direct(f.node)}
        changed = True
        while changed:
            changed = False
            for f in self.fns:
                if f.name not in found and transitive(f.node, found):
                    found.add(f.name)
                    changed = True
        return frozenset(found)

    @staticmethod
    def _direct_wrapper(fn: ast.AST) -> bool:
        for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call) and n.args):
            first = call.args[0]
            if (
                isinstance(first, (ast.List, ast.Tuple))
                and first.elts
                and _const(first.elts[0]) in ("gh", "gh.exe")
                and any(isinstance(e, ast.Starred) for e in first.elts[1:])
            ):
                return True
        return False

    @staticmethod
    def _passes_to_wrapper(fn: ast.AST, wrappers: set[str]) -> bool:
        return any(
            isinstance(n, ast.Call)
            and _call_name(n) in wrappers
            and n.args
            and isinstance(n.args[0], ast.Starred)
            for n in ast.walk(fn)
        )

    @staticmethod
    def _direct_sleeper(fn: ast.AST) -> bool:
        return any(
            isinstance(n, ast.Call) and _call_name(n) in _SLEEP_NAMES
            for n in ast.walk(fn)
        )

    @staticmethod
    def _calls_sleeper(fn: ast.AST, sleepers: set[str]) -> bool:
        return any(
            isinstance(n, ast.Call) and _call_name(n) in sleepers for n in ast.walk(fn)
        )

    def read_at(self, call: ast.Call, scope_strings: list[str]) -> PyRead | None:
        """Classify one call as a GitHub read, or None."""

        name = _call_name(call)
        strings = _strings(call)
        bounded = _bounded(strings) or _bounded(scope_strings)
        if call.args:
            first = call.args[0]
            argv: list[str] | None = None
            if (
                isinstance(first, (ast.List, ast.Tuple))
                and first.elts
                and _const(first.elts[0]) in ("gh", "gh.exe")
            ):
                argv = [c for c in (_const(e) for e in first.elts[1:]) if c is not None]
                if any(isinstance(e, ast.Starred) for e in first.elts[1:]):
                    return None  # the wrapper itself, judged at its call sites
            elif name in self.wrappers:
                if isinstance(first, ast.Starred):
                    return None
                if _const(first) is None:
                    return PyRead(call.lineno, f"{name}(...)", bounded, literal=False)
                argv = [c for c in (_const(a) for a in call.args) if c is not None]
            if argv is not None:
                if _is_watch(argv):
                    return PyRead(
                        call.lineno,
                        "gh " + " ".join(argv[:2]) + " (watch)",
                        False,
                        literal=True,
                    )
                kind = _gh_read_kind(argv)
                return (
                    PyRead(call.lineno, kind, bounded, literal=True) if kind else None
                )
        if self.mentions_api and (
            name in _HTTP_FUNCS
            or (
                name in ("get", "request")
                and (
                    _receiver(call) in _HTTP_RECEIVERS
                    or "session" in _receiver(call).lower()
                )
            )
        ):
            return PyRead(call.lineno, "api.github.com", bounded, literal=True)
        return None

    def reads_in(self, node: ast.AST, scope_strings: list[str]) -> list[PyRead]:
        out: list[PyRead] = []
        for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
            read = self.read_at(call, scope_strings)
            if read is not None:
                out.append(read)
        return out

    def reachable(
        self,
        node: ast.AST,
        scope_strings: list[str],
        seen: frozenset[str] = frozenset(),
    ) -> list[PyRead]:
        """Reads in `node` plus, through same-module calls, in its callees."""

        out = self.reads_in(node, scope_strings)
        for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
            name = _call_name(call)
            if name not in self.fn_names or name in seen or name in self.wrappers:
                continue
            out.extend(self._fn_reach(name, seen | {name}))
        return out

    def _fn_reach(self, name: str, seen: frozenset[str]) -> tuple[PyRead, ...]:
        if name in self._reach:
            return self._reach[name]
        out: list[PyRead] = []
        for f in self.fns:
            if f.name == name:
                out.extend(self.reachable(f.node, _strings(f.node), seen))
        result = tuple(out)
        self._reach[name] = result
        return result


def _enclosing_strings(tree: ast.Module, loop: ast.stmt) -> list[str]:
    for fn in (
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        if fn.lineno <= loop.lineno <= (fn.end_lineno or fn.lineno):
            return _strings(fn)
    return []


def _watch_findings(mod: PyModule, tree: ast.AST, rel: str, line_allowed, reported: set[int]) -> list[Finding]:
    """`gh ... watch` reads: gh's own polling loop, a finding wherever it appears."""
    findings: list[Finding] = []
    for read in mod.reads_in(tree, []):
        if read.what.endswith("(watch)") and not line_allowed(read.line):
            reported.add(read.line)
            findings.append(
                Finding(
                    rule=RULE,
                    path=rel,
                    line=read.line,
                    message=f"line {read.line}: `{read.what}` is gh's own polling loop -- it re-reads the whole "
                    "run / check set every few seconds with no bound",
                    fix=_FIX,
                )
            )
    return findings


def _sleeping_loop_reads(mod: PyModule, tree: ast.AST, loop, line_allowed, reported: set[int]) -> list:
    """GitHub reads a sleeping loop reaches that are not already reported or excused."""
    body = ast.Module(body=list(loop.body), type_ignores=[])
    if not any(
        isinstance(n, ast.Call) and _call_name(n) in (_SLEEP_NAMES | mod.sleepers)
        for n in ast.walk(body)
    ):
        return []
    if line_allowed(loop.lineno):
        return []
    reads = [
        r
        for r in mod.reachable(loop, _enclosing_strings(tree, loop))
        if not r.what.endswith("(watch)")
    ]
    return [r for r in reads if r.line not in reported and not line_allowed(r.line)]


def _loop_finding(loop, rel: str, unbounded: list, ambiguous: list) -> Finding:
    polling = isinstance(loop, ast.While)
    if polling and unbounded:
        status = Status.VIOLATION
        what = ", ".join(f"`{r.what}` (line {r.line})" for r in unbounded[:5])
        more = f" and {len(unbounded) - 5} more" if len(unbounded) > 5 else ""
        message = f"line {loop.lineno}: a sleeping `while` loop re-reads GitHub state with no time/cursor bound: {what}{more}"
    else:
        status = Status.NEEDS_REVIEW
        cited = unbounded or ambiguous
        what = ", ".join(f"`{r.what}` (line {r.line})" for r in cited[:5])
        why = (
            "a counted `for` loop that sleeps: a retry or a poll -- cannot tell statically"
            if not polling
            else "its reads go through a non-literal command -- cannot see the endpoint or a bound"
        )
        message = f"line {loop.lineno}: GitHub reads in a sleeping loop ({why}): {what}"
    return Finding(rule=RULE, path=rel, line=loop.lineno, status=status, message=message, fix=_FIX)


def check_python(path: Path, rel: str, *, gate_012_scope: bool) -> list[Finding]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError, ValueError):
        return []
    mod = PyModule(tree, source)

    def line_allowed(line: int) -> bool:
        return 0 < line <= len(mod.lines) and allowed(mod.lines[line - 1], RULE)

    reported: set[int] = set()
    findings = _watch_findings(mod, tree, rel, line_allowed, reported)
    loops = sorted(
        (n for n in ast.walk(tree) if isinstance(n, (ast.While, ast.For, ast.AsyncFor))),
        key=lambda n: n.lineno,
    )
    for loop in loops:
        reads = _sleeping_loop_reads(mod, tree, loop, line_allowed, reported)
        if not reads:
            continue
        if gate_012_scope and _names_app_check(ast.get_source_segment(source, loop) or ""):
            continue  # GATE-012 owns a wait on an app check
        unbounded = sorted(
            {r.line: r for r in reads if not r.bounded and r.literal}.values(),
            key=lambda r: r.line,
        )
        ambiguous = [r for r in reads if not r.bounded and not r.literal]
        if not unbounded and not ambiguous:
            continue  # every read the loop reaches is bounded
        reported.update(r.line for r in reads)
        findings.append(_loop_finding(loop, rel, unbounded, ambiguous))
    return findings


# ── entry point ───────────────────────────────────────────────────────────


def _is_tool_file(rel: str) -> bool:
    parts = rel.split("/")
    return rel.startswith(".claude/") or any(
        part in TOOL_DIR_NAMES for part in parts[:-1]
    )


def _script_findings(repo_root: Path, rel: str) -> list[Finding]:
    path = repo_root / rel
    gate_012_scope = rel.startswith("ci/")
    if rel.endswith(".py"):
        return check_python(path, rel, gate_012_scope=gate_012_scope)
    lines = raw_lines_of(path)
    return check_shell(
        ShellSource("\n".join(lines), rel, "script", tuple(lines), gate_012_scope)
    )


def _run_block_findings(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for wf in load_workflows(repo_root):
        if wf.status != LoadStatus.OK:
            continue
        raw = tuple(raw_lines_of(repo_root / wf.path))
        for job_id, job in jobs_of(as_dict(wf.document)).items():
            for i, step in enumerate(steps_of(job)):
                run = step.get("run")
                if isinstance(run, str):
                    findings.extend(
                        check_shell(
                            ShellSource(
                                run, wf.path, f"jobs.{job_id}.steps[{i}]", raw, True
                            )
                        )
                    )
    for act in load_composite_actions(repo_root):
        if act.status != LoadStatus.OK:
            continue
        raw = tuple(raw_lines_of(repo_root / act.path))
        steps = as_dict(as_dict(act.document).get("runs")).get("steps")
        for i, step in enumerate(as_dict(s) for s in as_list(steps)):
            run = step.get("run")
            if isinstance(run, str):
                findings.extend(
                    check_shell(
                        ShellSource(run, act.path, f"runs.steps[{i}]", raw, True)
                    )
                )
    return findings


def scanned_scripts(repo_root: Path) -> list[str]:
    """`ci/` scripts plus skill/tool scripts, repo-relative and sorted."""

    out: list[str] = []
    for rel in list_repo_files(repo_root):
        suffix = Path(rel).suffix
        if suffix != ".py" and suffix not in SHELL_SUFFIXES:
            continue
        if (rel.startswith("ci/") or _is_tool_file(rel)) and (
            repo_root / rel
        ).is_file():
            out.append(rel)
    return out


def check_ghapi_001(repo_root: Path) -> list[Finding]:
    findings = _run_block_findings(repo_root)
    for rel in scanned_scripts(repo_root):
        findings.extend(_script_findings(repo_root, rel))
    return findings
