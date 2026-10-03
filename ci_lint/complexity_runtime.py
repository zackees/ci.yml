"""Run Ruff against the explicit repository Python inventory (PY-004).

A configured rule is not evidence that a narrow lint invocation covers the
installer, benchmarks or helper scripts. This optional runtime audit supplies
all tracked Python files explicitly, bypassing directory discovery exclusions.
Ruff remains an external developer tool, not a ci-lint package dependency.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ci_lint.proc import Captured, run_captured
from ci_lint.repo_files import list_repo_files


def check_python_runtime(repo_root: Path) -> Captured:
    files = [rel for rel in list_repo_files(repo_root) if rel.endswith(".py")]
    if not files:
        return Captured(0, "", "")
    output: list[str] = []
    errors: list[str] = []
    verdict = 0
    # Bound argv size even in large fleet repositories. Preserve the normal
    # configured rules so RUF100 recognizes their existing line suppressions.
    for offset in range(0, len(files), 100):
        argv = [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "--no-force-exclude",
            "--extend-select",
            "C901,RUF100",
            "--",
            *files[offset : offset + 100],
        ]
        try:
            result = run_captured(argv, cwd=repo_root, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Captured(2, "".join(output), "".join(errors) + f"Python complexity audit unavailable: {exc}\n")
        output.append(result.stdout)
        errors.append(result.stderr)
        verdict = max(verdict, result.returncode)
    return Captured(verdict, "".join(output), "".join(errors))
