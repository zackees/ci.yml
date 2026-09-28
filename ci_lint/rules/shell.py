"""Group 3: shell budget (GEN-005).

Every `run:` step is at most one line calling a single allowlisted command
(round-1A brief; see also the worker contract's "YAML `run:` steps are ONE
line" rule). This module also bans `.sh/.ps1/.bat/.cmd` files and
extensionless scripts with a bash/sh shebang anywhere in the tracked tree,
and `shell: pwsh|powershell|cmd`. The draft ci.toml's `[[exceptions]]` entry
for `ci.sh` (GEN-005) is expected to turn exactly that finding into
`approved_exception` once `ci_lint.exceptions` runs.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ci_lint.finding import Finding, Status
from ci_lint.schema import CiToml
from ci_lint.workflow_scan import (
    ParsedYamlFile,
    as_dict,
    as_list,
    jobs_of,
    load_composite_actions,
    load_workflows,
    steps_of,
)
from ci_lint.yaml_io import LoadStatus

SCRIPT_EXTS: frozenset[str] = frozenset({".sh", ".ps1", ".bat", ".cmd"})
BANNED_SHELLS: frozenset[str] = frozenset({"pwsh", "powershell", "cmd"})
CONTROL_TOKENS: tuple[str, ...] = ("&&", "||", ";", "|", "$(", "`", "<<")
LEADING_CONTROL_RE = re.compile(r"^\s*(if|for|while)\s")


def _run_step_finding(run_text: str, path: str, loc: str) -> Finding | None:
    lines = [line for line in run_text.splitlines() if line.strip()]
    reasons: list[str] = []
    if len(lines) > 1:
        reasons.append("more than one line")
    found_tokens = [t for t in CONTROL_TOKENS if t in run_text]
    if found_tokens:
        reasons.append(f"control syntax ({', '.join(found_tokens)})")
    if any(LEADING_CONTROL_RE.match(line) for line in lines):
        reasons.append("leading if/for/while")
    if not reasons:
        return None
    return Finding(
        rule="GEN-005",
        path=path,
        message=f"{loc}: run: step violates the shell budget ({'; '.join(reasons)})",
        fix=f"replace {loc} with a single call to a Python script, e.g. "
        "'run: python3 ci/<script>.py ...' (or 'uv run --no-project --script ...'); move any "
        "control flow into that script",
    )


def _shell_key_finding(value: object, path: str, loc: str) -> Finding | None:
    if isinstance(value, str) and value.lower() in BANNED_SHELLS:
        return Finding(
            rule="GEN-005",
            path=path,
            message=f"{loc}: shell: '{value}' is banned",
            fix=f"remove 'shell: {value}' at {loc}; use the default shell to call a Python "
            "script (python3 on every platform, or 'uv run --no-project --script ...')",
        )
    return None


def _nested(doc: object, *keys: str) -> object:
    cur = doc
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _check_steps(
    findings: list[Finding], path: str, job_prefix: str, steps: list[dict[str, object]]
) -> None:
    for i, step in enumerate(steps):
        loc = f"{job_prefix}.steps[{i}]"
        run_text = step.get("run")
        if isinstance(run_text, str):
            f = _run_step_finding(run_text, path, loc)
            if f is not None:
                findings.append(f)
        f = _shell_key_finding(step.get("shell"), path, f"{loc}.shell")
        if f is not None:
            findings.append(f)


def check_tracked_scripts(repo_root: Path) -> list[Finding]:
    # --others --cached --exclude-standard: every file that is tracked OR
    # would become tracked on `git add .` (i.e. not gitignored). A fresh
    # Actions checkout has everything tracked already; this form also lets
    # an on-disk fixture tree be scanned without a separate `git add` step.
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--others", "--cached", "--exclude-standard"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except OSError as exc:
        return [
            Finding(
                rule="GEN-005",
                status=Status.NEEDS_REVIEW,
                message=f"'git ls-files' failed ({exc}); cannot scan tracked files for shell/batch scripts",
                fix="ensure git is on PATH and repo_root is a git worktree",
            )
        ]
    if proc.returncode != 0:
        return [
            Finding(
                rule="GEN-005",
                status=Status.NEEDS_REVIEW,
                message=f"'git ls-files' exited {proc.returncode}: {proc.stderr.strip()}",
                fix="ensure repo_root is a git worktree with a valid index",
            )
        ]

    findings: list[Finding] = []
    for rel in proc.stdout.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        rel_path = Path(rel)
        if rel_path.suffix.lower() in SCRIPT_EXTS:
            findings.append(
                Finding(
                    rule="GEN-005",
                    path=rel,
                    message=f"tracked script file '{rel}' ({rel_path.suffix}) -- CI logic must live "
                    "in Python, not shell/batch/PowerShell files",
                    fix=f"delete {rel} and move its logic into ci/<name>.py, invoked as a single "
                    "'run: python3 ci/<name>.py ...' step",
                )
            )
            continue
        if rel_path.suffix == "":
            full = repo_root / rel_path
            try:
                with full.open("rb") as fh:
                    first_line = fh.readline(200)
            except OSError:
                continue
            if first_line.startswith(b"#!") and (
                b"bash" in first_line or b"/sh" in first_line or first_line.rstrip().endswith(b"sh")
            ):
                findings.append(
                    Finding(
                        rule="GEN-005",
                        path=rel,
                        message=f"tracked extensionless file '{rel}' has a bash/sh shebang",
                        fix=f"delete {rel} and move its logic into ci/<name>.py",
                    )
                )
    return findings


def check_group3(ci: CiToml, repo_root: Path) -> list[Finding]:
    findings: list[Finding] = check_tracked_scripts(repo_root)

    workflows: list[ParsedYamlFile] = load_workflows(repo_root)
    actions: list[ParsedYamlFile] = load_composite_actions(repo_root)

    for f in workflows + actions:
        if f.status == LoadStatus.NEEDS_REVIEW:
            findings.append(
                Finding(
                    rule="GEN-005",
                    path=f.path,
                    status=Status.NEEDS_REVIEW,
                    message=f"cannot evaluate GEN-005 for {f.path}: {f.reason}",
                    fix="make PyYAML importable or `yq` available on PATH so ci-lint can parse "
                    f"{f.path}, then re-run precheck",
                )
            )

    for wf in workflows:
        if wf.status != LoadStatus.OK:
            continue
        doc = as_dict(wf.document)
        f = _shell_key_finding(_nested(doc, "defaults", "run", "shell"), wf.path, "defaults.run.shell")
        if f is not None:
            findings.append(f)
        for job_id, job in jobs_of(doc).items():
            f = _shell_key_finding(
                _nested(job, "defaults", "run", "shell"),
                wf.path,
                f"jobs.{job_id}.defaults.run.shell",
            )
            if f is not None:
                findings.append(f)
            _check_steps(findings, wf.path, f"jobs.{job_id}", steps_of(job))

    for act in actions:
        if act.status != LoadStatus.OK:
            continue
        runs = as_dict(act.document).get("runs")
        if isinstance(runs, dict):
            steps = [s for s in as_list(runs.get("steps")) if isinstance(s, dict)]
            _check_steps(findings, act.path, "runs", steps)

    return findings
