"""RUST-018 / PY-004: the function-complexity ratchet.

Every function stays under the linter's default complexity ceiling, and
the functions already over it are listed one by one, so the list can only
shrink. No new tool: the gate is the clippy and ruff run every Rust and
Python repository already has (`GEN-004`, the Dylint/clippy lane).

RUST-018 (static, Rust repositories -- a root `Cargo.toml`):
- the workspace (or the lone package) sets `clippy::cognitive_complexity`
  and `clippy::too_many_lines` to `warn`/`deny`/`forbid` -- clippy already
  runs with `-D warnings`, so either is a hard gate;
- every workspace member inherits them (`[lints] workspace = true`);
- no `clippy.toml` raises `cognitive-complexity-threshold` above 25 or
  `too-many-lines-threshold` above 100 (clippy's defaults);
- no source `allow(...)` names either lint, or the `clippy::pedantic` /
  `clippy::nursery` groups they belong to. A function over the ceiling
  carries `#[expect(clippy::too_many_lines, reason = "...")]` instead: an
  expectation that stops firing is itself a warning, so a refactored
  function's suppression must be deleted in the same change -- the ratchet.

PY-004 (static, repositories with tracked `*.py` files):
- ruff's `select`/`extend-select` enables `C901` and `RUF100`, neither is
  in `ignore`/`extend-ignore` or any `per-file-ignores` entry;
- `mccabe.max-complexity`, when set, is at most 10 (ruff's default);
- no file-level `# ruff: noqa` / `# flake8: noqa` silences a whole file.
  A function over the ceiling carries a line-level `# noqa: C901`; RUF100
  flags one that no longer suppresses anything -- the ratchet.

`ci-lint complexity [--repo .]` runs both without a `ci.toml`, so a
repository adopts the ratchet before it adopts schema 3; precheck group 19
runs them for a schema-3 repository.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tokenize
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ci_lint.finding import Finding, Status
from ci_lint.repo_files import list_repo_files
from ci_lint.rust_lexer import find_attribute_and_macro_spans, strip_comments_and_strings
from ci_lint.toml_cursor import TomlValue

RUST_RULE = "RUST-018"
PY_RULE = "PY-004"

RUST_LINTS: tuple[str, ...] = ("cognitive_complexity", "too_many_lines")
# clippy.toml key -> clippy's default, the loosest value the policy allows.
RUST_THRESHOLDS: dict[str, int] = {
    "cognitive-complexity-threshold": 25,
    "cyclomatic-complexity-threshold": 25,  # deprecated alias of the key above
    "too-many-lines-threshold": 100,
}
_ON_LEVELS = frozenset({"warn", "deny", "forbid"})
_BANNED_ALLOW = re.compile(r"clippy\s*::\s*(cognitive_complexity|too_many_lines|pedantic|nursery)\b")
_ALLOW_ATTR = re.compile(r"^#!?\[\s*(?:cfg_attr\s*\(.*,\s*)?allow\s*\(")
_EXPECT_ATTR = re.compile(r"expect\s*\([^)]*clippy\s*::\s*(?:cognitive_complexity|too_many_lines)\b")

PY_MAX_COMPLEXITY = 10
_FILE_NOQA = re.compile(r"^\s*#\s*(?:ruff|flake8)\s*:\s*noqa\b\s*(?::(?P<codes>.*))?$", re.IGNORECASE)
_LINE_NOQA_C901 = re.compile(r"#\s*noqa\s*:[^#\n]*\bC901\b")

RUST_FIX = (
    'set `cognitive_complexity = "warn"` and `too_many_lines = "warn"` under `[workspace.lints.clippy]`, '
    "give every member `[lints] workspace = true`, keep clippy.toml's thresholds at or below the defaults "
    '(25 / 100), and mark each existing offender `#[expect(clippy::<lint>, reason = "...")]` -- never '
    "`allow` (docs/policy-rust.md, RUST-018)"
)
PY_FIX = (
    "add `C901` and `RUF100` to `[tool.ruff.lint] select`, keep `[tool.ruff.lint.mccabe] max-complexity` "
    "at or below 10, and mark each existing offender with a line-level `# noqa: C901` on its `def` -- "
    "never a per-file or whole-file waiver (docs/policy-general.md, PY-004)"
)


@dataclass(frozen=True)
class RatchetScan:
    """One ratchet's findings and how many functions it currently excuses."""

    findings: tuple[Finding, ...]
    excused: int


@dataclass(frozen=True)
class RatchetDebt:
    """How many functions each ratchet currently excuses -- the number a
    refactor drives down."""

    rust_expects: int
    py_noqas: int


def _load_toml(path: Path) -> dict[str, TomlValue] | None:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None


def _table(doc: dict[str, TomlValue], *keys: str) -> dict[str, TomlValue]:
    node: TomlValue = doc
    for key in keys:
        if not isinstance(node, dict):
            return {}
        node = node.get(key)
    return node if isinstance(node, dict) else {}


def _level(value: TomlValue) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        level = value.get("level")
        return level if isinstance(level, str) else None
    return None


# ── RUST-018 ────────────────────────────────────────────────────────────────


def _check_lint_table(lints: dict[str, TomlValue], where: str, rel: str) -> list[Finding]:
    findings: list[Finding] = []
    for lint in RUST_LINTS:
        level = _level(lints.get(lint))
        if level not in _ON_LEVELS:
            shown = "unset" if level is None else f"`{level}`"
            findings.append(
                Finding(
                    rule=RUST_RULE,
                    path=rel,
                    message=f"`clippy::{lint}` is {shown} in `{where}`, so a new function can grow past the "
                    "complexity ceiling unnoticed",
                    fix=RUST_FIX,
                )
            )
    return findings


def _member_dirs(repo_root: Path, workspace: dict[str, TomlValue]) -> list[Path]:
    members = workspace.get("members")
    excluded_raw = workspace.get("exclude")
    excluded = {
        (repo_root / e).resolve()
        for e in (excluded_raw if isinstance(excluded_raw, list) else [])
        if isinstance(e, str)
    }
    dirs: list[Path] = []
    for pattern in members if isinstance(members, list) else []:
        if not isinstance(pattern, str):
            continue
        for path in sorted(repo_root.glob(pattern)):
            if path.resolve() not in excluded and (path / "Cargo.toml").is_file():
                dirs.append(path)
    return dirs


def _check_member_inherits(repo_root: Path, manifest: Path) -> list[Finding]:
    doc = _load_toml(manifest)
    if doc is None:
        return []
    rel = manifest.relative_to(repo_root).as_posix()
    lints = doc.get("lints")
    if isinstance(lints, dict) and lints.get("workspace") is True:
        return []
    return [
        Finding(
            rule=RUST_RULE,
            path=rel,
            message="this crate does not inherit the workspace lints (`[lints] workspace = true`), so the "
            "complexity ceiling does not apply to it",
            fix=RUST_FIX,
        )
    ]


def _check_manifests(repo_root: Path) -> list[Finding]:
    root_manifest = repo_root / "Cargo.toml"
    doc = _load_toml(root_manifest)
    if doc is None:
        return []
    workspace = _table(doc, "workspace")
    if not workspace:
        return _check_lint_table(_table(doc, "lints", "clippy"), "[lints.clippy]", "Cargo.toml")
    findings = _check_lint_table(_table(workspace, "lints", "clippy"), "[workspace.lints.clippy]", "Cargo.toml")
    if "package" in doc:
        findings.extend(_check_member_inherits(repo_root, root_manifest))
    for member in _member_dirs(repo_root, workspace):
        if member.resolve() != repo_root.resolve():
            findings.extend(_check_member_inherits(repo_root, member / "Cargo.toml"))
    return findings


def _check_clippy_toml(repo_root: Path, rel: str) -> list[Finding]:
    doc = _load_toml(repo_root / rel)
    if doc is None:
        return []
    findings: list[Finding] = []
    for key, ceiling in RUST_THRESHOLDS.items():
        value = doc.get(key)
        if isinstance(value, int) and value > ceiling:
            findings.append(
                Finding(
                    rule=RUST_RULE,
                    path=rel,
                    message=f"`{key} = {value}` raises clippy's ceiling above its default of {ceiling}",
                    fix=f"delete `{key}` (or set it to {ceiling} or lower) and mark the functions it excused "
                    "`#[expect(...)]` one by one",
                )
            )
    return findings


def scan_rust_source(text: str, rel: str) -> RatchetScan:
    """Return the banned `allow`s in one `.rs` file and how many functions
    it excuses with an `expect`."""

    findings: list[Finding] = []
    expects = 0
    for span in find_attribute_and_macro_spans(strip_comments_and_strings(text)):
        if _EXPECT_ATTR.search(span.text):
            expects += 1
        if not _ALLOW_ATTR.match(span.text):
            continue
        hit = _BANNED_ALLOW.search(span.text)
        if hit is None:
            continue
        findings.append(
            Finding(
                rule=RUST_RULE,
                path=rel,
                line=span.line,
                message=f"`{span.text}` silences `clippy::{hit.group(1)}` for good, so the function never "
                "has to shrink",
                fix="replace `allow` with `expect` (and a `reason`); an expectation that stops firing fails "
                "clippy, so the suppression is removed when the function is fixed (RUST-018)",
            )
        )
    return RatchetScan(findings=tuple(findings), excused=expects)


def check_rust_018(repo_root: Path) -> RatchetScan:
    if not (repo_root / "Cargo.toml").is_file():
        return RatchetScan(findings=(), excused=0)
    findings = _check_manifests(repo_root)
    expects = 0
    for rel in list_repo_files(repo_root):
        name = rel.rsplit("/", 1)[-1]
        if name in {"clippy.toml", ".clippy.toml"}:
            findings.extend(_check_clippy_toml(repo_root, rel))
        elif rel.endswith(".rs"):
            try:
                text = (repo_root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            scan = scan_rust_source(text, rel)
            findings.extend(scan.findings)
            expects += scan.excused
    return RatchetScan(findings=tuple(findings), excused=expects)


# ── PY-004 ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RuffConfig:
    path: str
    lint: dict[str, TomlValue]


def _find_ruff_config(repo_root: Path) -> RuffConfig | None:
    for name in ("ruff.toml", ".ruff.toml"):
        doc = _load_toml(repo_root / name)
        if doc is not None:
            merged = {k: v for k, v in doc.items() if k != "lint"}
            merged.update(_table(doc, "lint"))
            return RuffConfig(path=name, lint=merged)
    doc = _load_toml(repo_root / "pyproject.toml")
    if doc is None:
        return None
    ruff = _table(doc, "tool", "ruff")
    if not ruff:
        return None
    merged = {k: v for k, v in ruff.items() if k != "lint"}
    merged.update(_table(ruff, "lint"))
    return RuffConfig(path="pyproject.toml", lint=merged)


def _codes(lint: dict[str, TomlValue], *keys: str) -> list[str]:
    out: list[str] = []
    for key in keys:
        value = lint.get(key)
        if isinstance(value, list):
            out.extend(v.strip().upper() for v in value if isinstance(v, str))
    return out


def _covers(selectors: list[str], code: str) -> bool:
    return any(s == "ALL" or code.startswith(s) for s in selectors if s)


def _check_ruff_config(config: RuffConfig) -> list[Finding]:
    findings: list[Finding] = []
    selected = _codes(config.lint, "select", "extend-select")
    ignored = _codes(config.lint, "ignore", "extend-ignore")
    for code in ("C901", "RUF100"):
        if not _covers(selected, code):
            findings.append(
                Finding(rule=PY_RULE, path=config.path, message=f"ruff does not select `{code}`", fix=PY_FIX)
            )
        elif _covers(ignored, code):
            findings.append(
                Finding(rule=PY_RULE, path=config.path, message=f"ruff ignores `{code}` repo-wide", fix=PY_FIX)
            )
    per_file = config.lint.get("per-file-ignores")
    for pattern, codes in per_file.items() if isinstance(per_file, dict) else []:
        listed = [c.strip().upper() for c in codes if isinstance(c, str)] if isinstance(codes, list) else []
        if _covers(listed, "C901"):
            findings.append(
                Finding(
                    rule=PY_RULE,
                    path=config.path,
                    message=f"`per-file-ignores` waives `C901` for every function under `{pattern}`",
                    fix=PY_FIX,
                )
            )
    mccabe = config.lint.get("mccabe")
    limit = mccabe.get("max-complexity") if isinstance(mccabe, dict) else None
    if isinstance(limit, int) and limit > PY_MAX_COMPLEXITY:
        findings.append(
            Finding(
                rule=PY_RULE,
                path=config.path,
                message=f"`mccabe.max-complexity = {limit}` raises ruff's ceiling above its default of "
                f"{PY_MAX_COMPLEXITY}",
                fix=PY_FIX,
            )
        )
    return findings


def scan_python_source(text: str, rel: str) -> RatchetScan:
    """Return the whole-file waivers in one `.py` file and how many
    functions it excuses with a line-level `# noqa: C901`."""

    findings: list[Finding] = []
    noqas = 0
    for comment in _comments(text):
        if _waives_c901_file_wide(comment.text):
            findings.append(
                Finding(
                    rule=PY_RULE,
                    path=rel,
                    line=comment.line,
                    message=f"`{comment.text.strip()}` silences every ruff rule, C901 included, for the whole file",
                    fix="delete the file-level waiver and put a line-level `# noqa: <code>` on each line that "
                    "needs one (PY-004)",
                )
            )
        elif _LINE_NOQA_C901.search(comment.text):
            noqas += 1
    return RatchetScan(findings=tuple(findings), excused=noqas)


def _waives_c901_file_wide(comment: str) -> bool:
    """`# ruff: noqa` (every rule) or `# ruff: noqa: C901` / `C90` / `C` --
    not `# ruff: noqa: E501`, which waives only what it names."""

    hit = _FILE_NOQA.match(comment)
    if hit is None:
        return False
    codes = hit.group("codes")
    if codes is None:
        return True
    return _covers([c.strip().upper() for c in re.split(r"[,\s]+", codes) if c.strip()], "C901")


@dataclass(frozen=True)
class Comment:
    line: int
    text: str


def _comments(text: str) -> list[Comment]:
    """Real `#` comments only -- a docstring or string literal that merely
    mentions `# noqa: C901` is not a suppression. A file the tokenizer
    rejects falls back to whole lines, which can only over-count."""

    try:
        return [
            Comment(line=tok.start[0], text=tok.string)
            for tok in tokenize.generate_tokens(io.StringIO(text).readline)
            if tok.type == tokenize.COMMENT
        ]
    except (tokenize.TokenError, SyntaxError):
        return [Comment(line=n, text=line) for n, line in enumerate(text.splitlines(), start=1)]


def check_py_004(repo_root: Path) -> RatchetScan:
    py_files = [rel for rel in list_repo_files(repo_root) if rel.endswith(".py")]
    if not py_files:
        return RatchetScan(findings=(), excused=0)
    config = _find_ruff_config(repo_root)
    if config is None:
        findings = [
            Finding(
                rule=PY_RULE,
                path="pyproject.toml",
                message="the repository has Python files but no ruff configuration, so nothing bounds function "
                "complexity",
                fix=PY_FIX,
            )
        ]
    else:
        findings = _check_ruff_config(config)
    noqas = 0
    for rel in py_files:
        try:
            text = (repo_root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        scan = scan_python_source(text, rel)
        findings.extend(scan.findings)
        noqas += scan.excused
    return RatchetScan(findings=tuple(findings), excused=noqas)


@dataclass(frozen=True)
class ComplexityReport:
    findings: tuple[Finding, ...]
    debt: RatchetDebt


def check_complexity(repo_root: Path) -> ComplexityReport:
    rust = check_rust_018(repo_root)
    py = check_py_004(repo_root)
    return ComplexityReport(
        findings=rust.findings + py.findings, debt=RatchetDebt(rust_expects=rust.excused, py_noqas=py.excused)
    )


# ── CLI ─────────────────────────────────────────────────────────────────────


def _cmd_complexity(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    report = check_complexity(repo_root)
    findings, debt = report.findings, report.debt
    if args.json:
        print(
            json.dumps(
                {
                    "findings": [
                        {
                            "rule": f.rule,
                            "status": f.status.value,
                            "path": f.path,
                            "line": f.line,
                            "message": f.message,
                            "fix": f.fix,
                        }
                        for f in findings
                    ],
                    "debt": {"rust_expects": debt.rust_expects, "py_noqas": debt.py_noqas},
                },
                indent=2,
            )
        )
    else:
        for finding in findings:
            print(finding.render())
    violations = sum(1 for f in findings if f.status == Status.VIOLATION)
    print(
        f"complexity: {violations} violation(s); ratchet debt: {debt.rust_expects} Rust `expect`, "
        f"{debt.py_noqas} Python `noqa: C901`",
        file=sys.stderr,
    )
    runtime_exit = 0
    if args.python_runtime:
        from ci_lint.complexity_runtime import check_python_runtime

        result = check_python_runtime(repo_root)
        print(result.stdout, end="", file=sys.stderr if args.json else sys.stdout)
        print(result.stderr, end="", file=sys.stderr)
        runtime_exit = result.returncode
    return max(1 if violations else 0, runtime_exit)


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("complexity", help="function-complexity ratchet: clippy and ruff ceilings (RUST-018, PY-004)")
    p.add_argument("--repo", default=".")
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--python-runtime",
        action="store_true",
        help="run installed Ruff on every tracked Python file, including discovery-excluded files",
    )
    p.set_defaults(func=_cmd_complexity)
