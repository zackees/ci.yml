"""Run a gate with a fresh replay report and reject unproved successful exits."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import BinaryIO

from ci_lint.cargo_messages import JsonValue
from ci_lint.workflow_replay import ReplayExpectation, ReplayJob, ReplayInput, prove_replay
from ci_lint.workflow_replay_config import ReplayConfig, DeclaredReplayJob
from ci_lint.workflow_replay_expansion import expand_selection
from ci_lint.workflow_replay_inputs import BoundInput
from ci_lint.workflow_replay_checks import derive_checks
from ci_lint.workflow_scan import load_workflows

REPORT_ENV = "CI_LINT_GATE_REPLAY_REPORT"
MAX_REPORT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class CheckedCommand:
    returncode: int
    error: str | None = None


def _unique_object(pairs: Iterable[Sequence[JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for pair in pairs:
        key = pair[0]
        if not isinstance(key, str) or key in result:
            raise ValueError("replay report has duplicate or invalid JSON keys")
        result[key] = pair[1]
    return result


def _read_report(path: Path) -> JsonValue:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_REPORT_BYTES:
        raise ValueError("replay report must be a regular file no larger than 16 MiB")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


def _execute(repo: Path, argv: tuple[str, ...], report: Path, *, report_source: str,
             env: dict[str, str], stdout: BinaryIO | None) -> int:
    if report_source == "file":
        return subprocess.run(list(argv), cwd=repo, env=env, stdin=subprocess.DEVNULL,
                              stdout=stdout, stderr=subprocess.STDOUT if stdout is not None else None,
                              check=False).returncode
    if report_source != "stdout":
        raise ValueError("unknown replay report source")
    with report.open("wb") as receipt:
        process = subprocess.run(list(argv), cwd=repo, env=env, stdin=subprocess.DEVNULL,
                                 stdout=receipt, stderr=stdout, check=False)
    if stdout is not None:
        with report.open("rb") as receipt:
            shutil.copyfileobj(receipt, stdout)
    return process.returncode


def _required_jobs(repo: Path, declared: tuple[DeclaredReplayJob, ...], *, workflow: str,
                   selected: str | None, qualified: bool, inputs: tuple[ReplayInput, ...],
                   mode: str, event: str) -> tuple[ReplayJob, ...]:
    if not qualified:
        return tuple(job.proof for job in declared)
    expanded = expand_selection(tuple(load_workflows(repo)), workflow, selected, qualified=True,
                                inputs=tuple(BoundInput(item.name, item.value) for item in inputs))
    if expanded.problem:
        raise ValueError(expanded.problem)
    declarations = {job.source_job: job for job in declared}
    if len(declarations) != len(declared):
        raise ValueError("qualified replay has duplicate source-job declarations")
    if set(declarations) != {job.source_job for job in expanded.jobs}:
        raise ValueError("qualified replay declaration differs from the complete selected graph")
    return tuple(derive_checks(job, mode=mode, event=event) if declarations[job.source_job].derive_checks else
                 replace(declarations[job.source_job].proof, key=job.key, identity=job.identity)
                 for job in expanded.jobs)


def run_checked_command(repo: Path, argv: tuple[str, ...], config: ReplayConfig, *,
                        head: str, tree: str, lane: str | None = None,
                        env: dict[str, str] | None = None, stdout: BinaryIO | None = None) -> CheckedCommand:
    jobs = tuple(job for job in config.jobs if lane is None or lane in job.lanes)
    if not jobs:
        return CheckedCommand(1, f"no workflow replay proof declared for lane {lane}")
    refs = {job.source_job for job in jobs}
    selected = next(iter(refs)).split(":", 1)[1] if lane is not None and len(refs) == 1 else None
    selection = next((item for item in config.selections if item.lane == lane), None)
    if lane is None and config.selections:
        return CheckedCommand(1, "multi-selection replay requires separate lane commands and reports")
    event = selection.event if selection is not None else "pull_request"
    workflow = selection.workflow if selection is not None and selection.workflow is not None else config.workflow
    chosen = selection.selected_job if selection is not None else selected
    inputs = selection.inputs if selection is not None else ()
    try:
        required = _required_jobs(repo, jobs, workflow=workflow, selected=chosen, qualified=config.qualified,
                                  inputs=inputs, mode=config.mode, event=event)
    except (OSError, ValueError) as exc:
        return CheckedCommand(1, f"workflow replay proof rejected before execution: {exc}")
    expectation = ReplayExpectation(config.repository, repo, head, tree, workflow,
                                    chosen, config.mode, required,
                                    "pr" if event == "pull_request" else event, event,
                                    inputs)
    with tempfile.TemporaryDirectory(prefix="ci-replay-") as scratch:
        report = Path(scratch) / "report.json"
        child_env = dict(env) if env is not None else os.environ.copy()
        child_env[REPORT_ENV] = str(report)
        try:
            returncode = _execute(repo, argv, report, report_source=config.report_source,
                                  env=child_env, stdout=stdout)
            if returncode != 0:
                return CheckedCommand(returncode)
            prove_replay(_read_report(report), expectation)
        except (OSError, UnicodeError, ValueError) as exc:
            return CheckedCommand(1, f"workflow replay proof rejected: {exc}")
    return CheckedCommand(0)
