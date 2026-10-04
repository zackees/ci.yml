"""Strict TOML declaration of workflow jobs whose execution must be proved."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ci_lint.finding import Finding
from ci_lint.toml_cursor import Cursor, TomlValue
from ci_lint.workflow_replay import ReplayInput, ReplayJob

WORKFLOW = r"[A-Za-z0-9_.-]+\.ya?ml"
JOB_REF = rf"{WORKFLOW}:[A-Za-z0-9_-]+"


@dataclass(frozen=True)
class DeclaredReplayJob:
    source_job: str
    proof: ReplayJob
    lanes: tuple[str, ...]


@dataclass(frozen=True)
class ReplaySelection:
    lane: str
    selected_job: str | None
    event: str
    inputs: tuple[ReplayInput, ...]
    workflow: str | None = None


@dataclass(frozen=True)
class ReplayConfig:
    repository: str
    workflow: str
    mode: str
    jobs: tuple[DeclaredReplayJob, ...]
    selections: tuple[ReplaySelection, ...] = ()


def _bad(findings: list[Finding], source: str, path: str, message: str) -> None:
    findings.append(Finding(rule="GATE-001", path=source, message=f"{path}: {message}",
                            fix="declare the exact workflow jobs, executed check names and covered lanes"))


def _job(raw: dict[str, TomlValue], *, source: str, path: str,
         findings: list[Finding]) -> DeclaredReplayJob | None:
    start = len(findings)
    cursor = Cursor(raw, path, findings, source)
    ref = cursor.str_("source-job")
    key = cursor.str_("key")
    steps = cursor.list_str("steps")
    lanes = cursor.list_str("lanes", required=False)
    cursor.finish()
    if ref is None or re.fullmatch(JOB_REF, ref) is None:
        _bad(findings, source, path, "source-job must be a workflow basename and job id")
    if not key or not key.strip() or not steps or len(set(steps)) != len(steps) or any(not step.strip() for step in steps):
        _bad(findings, source, path, "key and distinct executed check names must be nonempty")
    if len(set(lanes)) != len(lanes) or any(not lane.strip() for lane in lanes):
        _bad(findings, source, path, "lane names must be nonempty and distinct")
    if len(findings) != start or ref is None or key is None:
        return None
    return DeclaredReplayJob(ref, ReplayJob(key, tuple(steps)), tuple(lanes))


def _selected_job(cursor: Cursor, source: str, path: str,
                  findings: list[Finding]) -> str | None:
    selected = cursor.str_("job", required=False)
    all_jobs = cursor.bool_("all-jobs", required=False, default=False)
    if all_jobs:
        if selected is not None:
            _bad(findings, source, path, "all-jobs and job are mutually exclusive")
    elif selected is None or re.fullmatch(r"[A-Za-z0-9_-]+", selected) is None:
        _bad(findings, source, path, "selection requires a literal job or all-jobs = true")
    return selected


def _selection(raw: dict[str, TomlValue], *, source: str, path: str,
               findings: list[Finding]) -> ReplaySelection | None:
    start = len(findings)
    cursor = Cursor(raw, path, findings, source)
    lane = cursor.str_("lane")
    selected = _selected_job(cursor, source, path, findings)
    workflow = cursor.str_("workflow", required=False)
    event = cursor.str_("event")
    inputs = cursor.dict_str_str("inputs", required=False)
    cursor.finish()
    if not lane or not lane.strip():
        _bad(findings, source, path, "selection requires a lane")
    if workflow is not None and re.fullmatch(WORKFLOW, workflow) is None:
        _bad(findings, source, path, "selection workflow must be a workflow basename")
    if event not in ("pull_request", "workflow_dispatch"):
        _bad(findings, source, path, "selection event must be pull_request or workflow_dispatch")
    if any(not key.strip() for key in inputs):
        _bad(findings, source, path, "input names must be nonempty")
    if len(findings) != start or lane is None or event is None:
        return None
    return ReplaySelection(lane, selected, event, tuple(ReplayInput(key, value) for key, value in inputs.items()),
                           f".github/workflows/{workflow}" if workflow is not None else None)


def _selections(cursor: Cursor, source: str, path: str,
                findings: list[Finding]) -> tuple[ReplaySelection, ...]:
    selections: list[ReplaySelection] = []
    for index, item in enumerate(cursor.array_of_tables("selections")):
        selection = _selection(item, source=source, path=f"{path}.selections[{index}]", findings=findings)
        if selection is not None:
            selections.append(selection)
    return tuple(selections)


def parse_replay(raw: dict[str, TomlValue], *, source: str, path: str,
                 findings: list[Finding]) -> ReplayConfig | None:
    start = len(findings)
    cursor = Cursor(raw, path, findings, source)
    repository = cursor.str_("repository")
    workflow = cursor.str_("workflow")
    mode = cursor.str_("mode")
    raw_jobs = cursor.array_of_tables("jobs")
    selections = _selections(cursor, source, path, findings)
    cursor.finish()
    if repository is None or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
        _bad(findings, source, path, "repository must be owner/name")
    if workflow is None or re.fullmatch(WORKFLOW, workflow) is None:
        _bad(findings, source, path, "workflow must be a workflow basename")
    if mode not in ("minimal", "full"):
        _bad(findings, source, path, "mode must be minimal or full")
    jobs: list[DeclaredReplayJob] = []
    for index, item in enumerate(raw_jobs):
        job = _job(item, source=source, path=f"{path}.jobs[{index}]", findings=findings)
        if job is not None:
            jobs.append(job)
    covered_lanes = {lane for job in jobs for lane in job.lanes}
    if len({item.lane for item in selections}) != len(selections) or any(item.lane not in covered_lanes for item in selections):
        _bad(findings, source, path, "selections must name distinct replay-covered lanes")
    if not jobs or len({job.proof.key for job in jobs}) != len(jobs):
        _bad(findings, source, path, "declare one or more distinct execution job keys")
    if len(findings) != start or repository is None or workflow is None or mode is None:
        return None
    return ReplayConfig(repository, f".github/workflows/{workflow}", mode, tuple(jobs), tuple(selections))
