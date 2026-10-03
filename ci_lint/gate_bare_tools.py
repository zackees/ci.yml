"""RUST-001 beyond `ci.toml`: no bare Rust toolchain on any CI or local-gate
surface of a Rust repository (zackees/ci.yml#170).

`ci_lint.rules.tools` already reports a bare `cargo`/`rustc`/`rustup`/
`maturin` in a `ci.toml` repository's workflows (TOOL-001 + RUST-001). Most
fleet repositories have no `ci.toml`, and the local gate added new surfaces
that run Rust: the gate's own argv and script, and the `bosn.toml` tasks it
runs isolated (GATE-005). A Rust project is always driven through soldr
(`soldr cargo ...`, `soldr rustup ...`), locally and remotely, so the pinned
toolchain, the compiler cache, and the Dylint wiring always apply; a bare
`cargo` silently gets whatever toolchain and cache the host happens to have.

Scanned, for a repository with a root `Cargo.toml`:

- every `run:` line in `.github/workflows/*` and composite actions;
- the local gate's `run` argv and every repository file it names: shell
  lines, and in Python, every string-literal argv (a list/tuple whose first
  element is a bare tool);
- every `[task.*].cmd` in `bosn.toml`.

Reported as RUST-001. Product source (the Rust code soldr itself is made
of) is out of scope: a tool that *is* the wrapper must call cargo.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

from ci_lint.yaml_io import YamlValue
from ci_lint.finding import Finding
from ci_lint.rules.test_invocations import allowed, raw_lines_of, split_commands, with_yaml_comment

BARE_RUST: frozenset[str] = frozenset({"cargo", "rustc", "rustup", "maturin", "cargo-nextest"})
FIX = "drive it through soldr ('soldr cargo ...', 'soldr rustup ...', 'soldr wheel ...') so the pinned toolchain and cache apply"


def _bare(tokens: list[str]) -> str | None:
    if not tokens:
        return None
    cmd = tokens[0].rsplit("/", 1)[-1]
    if len(tokens) > 1 and tokens[1].startswith(("=", "+=", ":=")):
        return None  # `cargo = tomllib.loads(...)`: an assignment, not a command
    return cmd if cmd in BARE_RUST else None


def _wrapped_by_soldr(cmd: str, line: str) -> bool:
    """`"$soldr" rustup which cargo` / `$(soldr rustup ...)`: the tool is an
    argument to soldr, not a bare invocation."""

    pattern = r"""soldr[A-Za-z0-9_]*["'}]*\s+""" + re.escape(cmd) + r"\b"
    return re.search(pattern, line, re.IGNORECASE) is not None


def _shell_findings(text: str, path: str, where: str, raw_lines: list[str] | None = None) -> list[Finding]:
    """A same-line `# ci-lint: allow RUST-001 <reason>` excuses one line
    (the TOOL-002 convention, ci.yml#138) -- for the genuine bootstrap cases:
    building soldr before any soldr exists, a deliberate bare-cargo baseline
    in a benchmark, a smoke test of soldr's own PATH shims."""

    findings = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if allowed(with_yaml_comment(line, raw_lines or []), "RUST-001"):
            continue
        # Quote-aware: `echo "cargo | foo"` is one echo, not a bare cargo.
        for tokens in split_commands(stripped):
            cmd = _bare(tokens)
            if cmd is not None and not _wrapped_by_soldr(cmd, stripped):
                findings.append(
                    Finding(rule="RUST-001", path=path, message=f"{where}: bare '{cmd}' (`{stripped[:100]}`)", fix=FIX)
                )
    return findings


def _python_findings(text: str, path: str) -> list[Finding]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    findings = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            first = node.elts[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                cmd = _bare([first.value])
                if cmd is not None:
                    findings.append(
                        Finding(rule="RUST-001", path=path, line=node.lineno,
                                message=f"argv starts with bare '{cmd}'", fix=FIX)
                    )
    return findings


def _step_findings(step: dict[str, YamlValue], path: str, where: str, raw: list[str]) -> list[Finding]:
    run = step.get("run")
    if not isinstance(run, str):
        return []
    shell = step.get("shell")
    if isinstance(shell, str) and shell.split()[0].rsplit("/", 1)[-1].startswith("python"):
        # A Python step: argv lists, not shell words (clud's auto-release.yml
        # `cargo = tomllib.loads(...)`).
        return _python_findings(run, path)
    return _shell_findings(run, path, where, raw)


def _workflow_findings(repo_root: Path) -> list[Finding]:
    from ci_lint.workflow_scan import as_dict, jobs_of, load_composite_actions, load_workflows, steps_of  # noqa: PLC0415
    from ci_lint.yaml_io import LoadStatus  # noqa: PLC0415

    findings: list[Finding] = []
    for wf in load_workflows(repo_root):
        if wf.status != LoadStatus.OK:
            continue
        raw = raw_lines_of(repo_root / wf.path)
        for job_id, job in jobs_of(as_dict(wf.document)).items():
            for step in steps_of(job):
                findings.extend(_step_findings(step, wf.path, f"job '{job_id}'", raw))
    for action in load_composite_actions(repo_root):
        if action.status != LoadStatus.OK:
            continue
        runs = as_dict(as_dict(action.document).get("runs"))
        steps = runs.get("steps")
        for step in steps if isinstance(steps, list) else []:
            run = step.get("run") if isinstance(step, dict) else None
            if isinstance(run, str):
                raw = raw_lines_of(repo_root / action.path)
                findings.extend(_shell_findings(run, action.path, "composite action", raw))
    return findings


def check_no_bare_rust(repo_root: Path, gate_run: tuple[str, ...], *, workflows: bool = True) -> list[Finding]:
    """`workflows=False` from `precheck`, whose own RUST-001
    (`ci_lint.rules.tools`) already covers a ci.toml repo's workflows."""

    if not (repo_root / "Cargo.toml").is_file():
        return []
    findings = _workflow_findings(repo_root) if workflows else []
    gate_cmd = _bare(list(gate_run))
    if gate_cmd is not None:
        findings.append(Finding(rule="RUST-001", path="local gate", message=f"the gate's argv is bare '{gate_cmd}'", fix=FIX))
    for token in gate_run:
        script = repo_root / token
        if not script.is_file():
            continue
        text = script.read_text(encoding="utf-8", errors="replace")
        rel = script.relative_to(repo_root).as_posix()
        findings.extend(_python_findings(text, rel) if script.suffix == ".py" else _shell_findings(text, rel, "gate script"))
    manifest = repo_root / "bosn.toml"
    if manifest.is_file():
        try:
            doc = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            doc = {}
        tasks = doc.get("task")
        for name, spec in (tasks.items() if isinstance(tasks, dict) else []):
            cmd = spec.get("cmd") if isinstance(spec, dict) else None
            if isinstance(cmd, str):
                findings.extend(_shell_findings(cmd, "bosn.toml", f"task '{name}'"))
    return findings
