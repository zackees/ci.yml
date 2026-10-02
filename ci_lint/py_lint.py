"""`ci-lint py lint`: fleet-usable PY-002/PY-003 with a ratchet baseline.

Runs over every tracked `*.py` file of a repository (`git ls-files`):

- PY-002 (`ci_lint.typed_records`): record-shaped tuple/dict annotations;
- PY-003 (`ci_lint.subprocess_capture`): pipe-captured subprocess output.

A repository adopting a rule with existing violations records them once
(`--write-baseline`) and the baseline becomes a ratchet: a file whose count
rises fails, and so does a baseline looser than reality (lower it with
`--write-baseline` after fixing sites), so counts only ever go down and new
files start at zero.

Exit codes: 0 clean against the baseline, 1 a violation or a loose
baseline, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from ci_lint.globs import matches_any
from ci_lint.proc import run_captured
from ci_lint.subprocess_capture import scan_source as scan_capture
from ci_lint.typed_records import scan_source as scan_records

RULES: tuple[str, ...] = ("PY-002", "PY-003")


@dataclass(frozen=True)
class PyFinding:
    rule: str
    path: str
    line: int
    text: str


@dataclass(frozen=True)
class FileCount:
    rule: str
    path: str
    count: int


@dataclass(frozen=True)
class RatchetResult:
    findings: tuple[PyFinding, ...]
    grown: tuple[FileCount, ...]  # current count above the baseline
    loose: tuple[FileCount, ...]  # baseline above the current count


def tracked_python(root: Path) -> list[str]:
    proc = run_captured(["git", "-C", str(root), "ls-files", "-z", "--", "*.py"])
    if not proc.ok:
        raise RuntimeError(f"git ls-files failed: {proc.stderr.strip()}")
    return sorted(p for p in proc.stdout.split("\0") if p)


def scan(root: Path, rules: tuple[str, ...], paths: list[str]) -> list[PyFinding]:
    out: list[PyFinding] = []
    for rel in paths:
        try:
            source = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "PY-002" in rules:
            out.extend(PyFinding("PY-002", v.path, v.line, f"{v.where} is `{v.annotation}`") for v in scan_records(rel, source))
        if "PY-003" in rules:
            out.extend(PyFinding("PY-003", v.path, v.line, f"{v.call}(...) {v.reason}") for v in scan_capture(rel, source))
    return out


def counts_of(findings: list[PyFinding]) -> list[FileCount]:
    tally: list[FileCount] = []
    for rule in sorted({f.rule for f in findings}):
        for path in sorted({f.path for f in findings if f.rule == rule}):
            tally.append(FileCount(rule, path, sum(1 for f in findings if f.rule == rule and f.path == path)))
    return tally


def load_baseline(path: Path) -> list[FileCount]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: list[FileCount] = []
    rules = raw.get("rules", {}) if isinstance(raw, dict) else {}
    for rule, files in rules.items():
        if isinstance(files, dict):
            out.extend(FileCount(rule, p, int(n)) for p, n in files.items() if isinstance(n, int))
    return out


def write_baseline(path: Path, counts: list[FileCount], rules: tuple[str, ...]) -> None:
    doc = {
        "_comment": "ci-lint py lint ratchet (PY-002 typed records, PY-003 no pipe-captured subprocess output; "
        "zackees/ci.yml). Counts may only go down; a file not listed must stay at 0. Regenerate with "
        "`ci-lint py lint --baseline <this file> --write-baseline` after fixing sites.",
        "rules": {rule: {c.path: c.count for c in counts if c.rule == rule} for rule in rules},
    }
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ratchet(findings: list[PyFinding], baseline: list[FileCount], rules: tuple[str, ...]) -> RatchetResult:
    current = counts_of(findings)

    def allowed(rule: str, path: str) -> int:
        return next((b.count for b in baseline if b.rule == rule and b.path == path), 0)

    def now(rule: str, path: str) -> int:
        return next((c.count for c in current if c.rule == rule and c.path == path), 0)

    grown = tuple(c for c in current if c.count > allowed(c.rule, c.path))
    loose = tuple(
        FileCount(b.rule, b.path, b.count) for b in baseline if b.rule in rules and now(b.rule, b.path) < b.count
    )
    grown_keys = {(g.rule, g.path) for g in grown}
    shown = tuple(f for f in findings if (f.rule, f.path) in grown_keys)
    return RatchetResult(findings=shown, grown=grown, loose=loose)


def _cmd_lint(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    rules = tuple(r.strip() for r in args.rules.split(",") if r.strip())
    unknown = [r for r in rules if r not in RULES]
    if unknown:
        print(f"ci-lint py lint: unknown rule(s) {', '.join(unknown)}; known: {', '.join(RULES)}", file=sys.stderr)
        return 2
    excluded = tuple(args.exclude or ())
    try:
        paths = [p for p in tracked_python(root) if not matches_any(p, excluded)]
        findings = scan(root, rules, paths)
    except RuntimeError as exc:
        print(f"ci-lint py lint: {exc}", file=sys.stderr)
        return 2
    baseline_path = Path(args.baseline) if args.baseline else None
    if args.write_baseline:
        if baseline_path is None:
            print("ci-lint py lint: --write-baseline needs --baseline FILE", file=sys.stderr)
            return 2
        write_baseline(baseline_path, counts_of(findings), rules)
        print(f"ci-lint py lint: wrote {baseline_path} ({len(findings)} recorded site(s))")
        return 0
    baseline = load_baseline(baseline_path) if baseline_path and baseline_path.is_file() else []
    result = ratchet(findings, baseline, rules)
    for f in result.findings:
        print(f"[{f.rule}] {f.path}:{f.line}: {f.text}")
    for g in result.grown:
        print(f"[{g.rule}] {g.path}: {g.count} site(s), baseline allows {next((b.count for b in baseline if b.rule == g.rule and b.path == g.path), 0)}")
    for loose in result.loose:
        print(f"[{loose.rule}] {loose.path}: baseline {loose.count} is above the current count -- lower it "
              f"(ci-lint py lint --baseline {baseline_path} --write-baseline)")
    fix = {
        "PY-002": "return a frozen @dataclass instead of a tuple/dict record",
        "PY-003": "use the running-process package (RunningProcess / subprocess_run), iterate a Popen pipe while the child runs, or capture to temporary files and forward on failure -- never capture-then-wait through a pipe",
    }
    if result.grown:
        for rule in sorted({g.rule for g in result.grown}):
            print(f"fix ({rule}): {fix[rule]}")
    print(f"ci-lint py lint: {len(findings)} site(s) total, {len(result.grown)} file(s) over baseline, "
          f"{len(result.loose)} loose baseline entr(ies)")
    return 1 if result.grown or result.loose else 0


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("py", help="Python policy checks (PY-002 typed records, PY-003 no pipe capture)")
    py_sub = p.add_subparsers(dest="py_command", required=True)
    lint = py_sub.add_parser("lint", help="scan tracked *.py files against a ratchet baseline")
    lint.add_argument("--root", default=".")
    lint.add_argument("--rules", default=",".join(RULES), help="comma-separated (default: all)")
    lint.add_argument("--baseline", help="ratchet baseline JSON (missing file = everything must be 0)")
    lint.add_argument("--write-baseline", action="store_true", help="record current counts as the baseline")
    lint.add_argument("--exclude", action="append", metavar="GLOB",
                      help="skip tracked files matching GLOB (repeatable), e.g. test fixtures that are other repos' data")
    lint.set_defaults(func=_cmd_lint)
