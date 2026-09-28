"""Group 4: tool rules.

TOOL-001 (no bare cargo/rustc/rustup/maturin/... -- use soldr/uv), TOOL-002
(a dep-resolving cargo subcommand needs --locked), and CACHE-009
(zackees/setup-soldr may only be called from its one wrapper action, with
its required inputs, and never enabling a retired cache family).
"""

from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path

from ci_lint.finding import Finding
from ci_lint.schema import CiToml
from ci_lint.workflow_scan import (
    as_dict,
    as_list,
    jobs_of,
    load_composite_actions,
    load_workflows,
    steps_of,
)
from ci_lint.yaml_io import LoadStatus, YamlValue

BARE_BANNED: frozenset[str] = frozenset(
    {
        "cargo",
        "rustc",
        "rustup",
        "maturin",
        "cross",
        "cibuildwheel",
        "pip",
        "pipx",
        "twine",
        "curl",
        "wget",
    }
)
LOCKED_SUBCOMMANDS: frozenset[str] = frozenset(
    {"build", "test", "check", "clippy", "doc", "run", "nextest"}
)
ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _is_bare_banned(cmd: str) -> bool:
    return cmd in BARE_BANNED or cmd.startswith("cargo-")


def find_commands(text: str) -> list[list[str]]:
    """Split a shell string on control separators, strip leading env
    assignments, and return each segment's remaining tokens."""

    segments = re.split(r"&&|\|\||[;|]", text)
    out: list[list[str]] = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        try:
            tokens = shlex.split(seg)
        except ValueError:
            continue
        i = 0
        while i < len(tokens) and ENV_ASSIGN_RE.match(tokens[i]):
            i += 1
        if i < len(tokens):
            out.append(tokens[i:])
    return out


def _tool_findings_for_commands(commands: list[list[str]], path: str, loc: str) -> list[Finding]:
    findings: list[Finding] = []
    for tokens in commands:
        if not tokens:
            continue
        cmd = tokens[0]
        cargo_tokens: list[str] | None = None
        if cmd == "soldr":
            # 'soldr cargo <subcommand> ...' still needs to be checked for
            # --locked; soldr itself is never a TOOL-001 violation.
            if len(tokens) > 1 and tokens[1] == "cargo":
                cargo_tokens = tokens[1:]
        else:
            if cmd == "cargo":
                cargo_tokens = tokens
            if _is_bare_banned(cmd):
                findings.append(
                    Finding(
                        rule="TOOL-001",
                        path=path,
                        message=f"{loc}: bare '{cmd}' invoked directly",
                        fix=f"wrap the Rust/wheel tool through soldr ('soldr cargo {cmd} ...' or "
                        "'soldr wheel ...') or through uv for Python packaging; never call it bare",
                    )
                )
        if (
            cargo_tokens is not None
            and len(cargo_tokens) > 1
            and cargo_tokens[1] in LOCKED_SUBCOMMANDS
            and "--locked" not in cargo_tokens
        ):
            findings.append(
                Finding(
                    rule="TOOL-002",
                    path=path,
                    message=f"{loc}: 'cargo {cargo_tokens[1]}' resolves dependencies without --locked",
                    fix=f"add '--locked' to the 'cargo {cargo_tokens[1]}' invocation at {loc}",
                )
            )
    return findings


def _iter_run_steps(repo_root: Path) -> list[tuple[str, str, str]]:
    """(run_text, path, loc) for every run: step in workflows and composite actions."""

    out: list[tuple[str, str, str]] = []
    for wf in load_workflows(repo_root):
        if wf.status != LoadStatus.OK:
            continue
        doc = as_dict(wf.document)
        for job_id, job in jobs_of(doc).items():
            for i, step in enumerate(steps_of(job)):
                run_text = step.get("run")
                if isinstance(run_text, str):
                    out.append((run_text, wf.path, f"jobs.{job_id}.steps[{i}]"))
    for act in load_composite_actions(repo_root):
        if act.status != LoadStatus.OK:
            continue
        runs = as_dict(act.document).get("runs")
        if isinstance(runs, dict):
            for i, step in enumerate(as_list(runs.get("steps"))):
                if isinstance(step, dict) and isinstance(step.get("run"), str):
                    out.append((step["run"], act.path, f"runs.steps[{i}]"))
    return out


def check_tool_rules_workflows(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for run_text, path, loc in _iter_run_steps(repo_root):
        commands = find_commands(run_text)
        findings.extend(_tool_findings_for_commands(commands, path, loc))
    return findings


SUBPROCESS_METHODS: frozenset[str] = frozenset({"run", "Popen", "call", "check_call", "check_output"})


def _root_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    return None


def _literal_str_list(node: ast.expr) -> list[str] | None:
    if isinstance(node, (ast.List, ast.Tuple)):
        out: list[str] = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                out.append(elt.value)
            else:
                return None
        return out
    return None


def _literal_str(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _discover_python_command_files(repo_root: Path) -> list[Path]:
    out: list[Path] = sorted(repo_root.glob("*.py"))
    ci_dir = repo_root / "ci"
    if ci_dir.is_dir():
        out.extend(sorted(ci_dir.rglob("*.py")))
    return out


def _extract_ast_commands(path: Path) -> list[tuple[list[str], int]]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    out: list[tuple[list[str], int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        matched = False
        if isinstance(func, ast.Attribute):
            root = _root_name(func.value)
            if root == "subprocess" and func.attr in SUBPROCESS_METHODS:
                matched = True
            elif root == "os" and func.attr in ("system", "spawnl", "spawnv", "spawnve"):
                matched = True
        if not matched or not node.args:
            continue
        first = node.args[0]
        tokens = _literal_str_list(first)
        if tokens is None:
            as_str = _literal_str(first)
            if as_str is not None:
                try:
                    tokens = shlex.split(as_str)
                except ValueError:
                    tokens = None
        if tokens:
            out.append((tokens, node.lineno))
    return out


def check_tool_rules_python(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in _discover_python_command_files(repo_root):
        rel = path.relative_to(repo_root).as_posix()
        for tokens, lineno in _extract_ast_commands(path):
            i = 0
            while i < len(tokens) and ENV_ASSIGN_RE.match(tokens[i]):
                i += 1
            remaining = tokens[i:]
            if not remaining:
                continue
            for f in _tool_findings_for_commands([remaining], rel, f"line {lineno}"):
                findings.append(
                    Finding(rule=f.rule, path=f.path, line=lineno, message=f.message, fix=f.fix)
                )
    return findings


def _truthy(value: YamlValue) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return False


def _iter_uses_with(
    doc: dict[str, YamlValue], *, is_composite: bool
) -> list[tuple[str, str, dict[str, YamlValue]]]:
    out: list[tuple[str, str, dict[str, YamlValue]]] = []
    if is_composite:
        runs = doc.get("runs")
        if isinstance(runs, dict):
            for i, step in enumerate(as_list(runs.get("steps"))):
                if isinstance(step, dict) and isinstance(step.get("uses"), str):
                    with_ = step.get("with")
                    out.append((step["uses"], f"runs.steps[{i}]", with_ if isinstance(with_, dict) else {}))
    else:
        for job_id, job in jobs_of(doc).items():
            for i, step in enumerate(steps_of(job)):
                uses = step.get("uses")
                if isinstance(uses, str):
                    with_ = step.get("with")
                    out.append(
                        (uses, f"jobs.{job_id}.steps[{i}]", with_ if isinstance(with_, dict) else {})
                    )
    return out


def check_cache_009(ci: CiToml, repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    only_in = ci.allow.setup_soldr.only_in.strip("/")

    files: list[tuple[str, bool, dict[str, YamlValue]]] = []
    for wf in load_workflows(repo_root):
        if wf.status == LoadStatus.OK:
            files.append((wf.path, False, as_dict(wf.document)))
    for act in load_composite_actions(repo_root):
        if act.status == LoadStatus.OK:
            files.append((act.path, True, as_dict(act.document)))

    for path, is_composite, doc in files:
        for uses, loc, with_ in _iter_uses_with(doc, is_composite=is_composite):
            if not uses.startswith("zackees/setup-soldr"):
                continue
            file_dir = Path(path).parent.as_posix()
            if file_dir != only_in:
                findings.append(
                    Finding(
                        rule="CACHE-009",
                        path=path,
                        message=f"{loc}: zackees/setup-soldr is called outside the allowed wrapper "
                        f"location '{only_in}' (found in '{file_dir}')",
                        fix=f"move this call into the wrapper composite action at {only_in}/action.yml "
                        f"and call that wrapper from {path} instead",
                    )
                )
                continue

            for key, expected in ci.allow.setup_soldr.require.items():
                actual = with_.get(key)
                if actual is None:
                    findings.append(
                        Finding(
                            rule="CACHE-009",
                            path=path,
                            message=f"{loc}: setup-soldr wrapper is missing required input '{key}'",
                            fix=f'add \'{key}: "{expected}"\' to {loc}\'s with: block',
                        )
                    )
                elif str(actual).lower() != expected.lower():
                    findings.append(
                        Finding(
                            rule="CACHE-009",
                            path=path,
                            message=f"{loc}: setup-soldr input '{key}' = {actual!r}, expected "
                            f"'{expected}'",
                            fix=f'set \'{key}: "{expected}"\' in {loc}\'s with: block',
                        )
                    )
            for key, val in with_.items():
                key_norm = key.lower().replace("_", "-")
                for retired in ci.cache.retired:
                    if retired.lower() in key_norm and _truthy(val):
                        findings.append(
                            Finding(
                                rule="CACHE-009",
                                path=path,
                                message=f"{loc}: input '{key}' = {val!r} enables retired cache "
                                f"family '{retired}'",
                                fix=f"remove or disable '{key}' in {loc}'s with: block; the "
                                f"'{retired}' cache family is retired (ci.toml [cache].retired)",
                            )
                        )
    return findings


def check_group4(ci: CiToml, repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(check_tool_rules_workflows(repo_root))
    findings.extend(check_tool_rules_python(repo_root))
    findings.extend(check_cache_009(ci, repo_root))
    return findings
