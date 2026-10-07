"""Resolve one replay invocation into complete source-bound execution proof."""

from dataclasses import dataclass, replace
from pathlib import Path

from ci_lint.workflow_replay import ReplayInput, ReplayJob
from ci_lint.workflow_replay_checks import derive_checks
from ci_lint.workflow_replay_config import DeclaredReplayJob, ReplayConfig, ReplaySelection
from ci_lint.workflow_replay_expansion import expand_selection
from ci_lint.workflow_replay_identity import JobIdentity
from ci_lint.workflow_replay_inputs import BoundInput
from ci_lint.workflow_scan import ParsedYamlFile, load_workflows


@dataclass(frozen=True)
class ReplayPlan:
    workflow: str
    selected: str | None
    event: str
    inputs: tuple[ReplayInput, ...]
    required: tuple[ReplayJob, ...]
    lanes: tuple[str, ...] = ()


def _declared(config: ReplayConfig, lane: str | None) -> tuple[DeclaredReplayJob, ...]:
    jobs = tuple(job for job in config.jobs if lane is None or lane in job.lanes)
    if not jobs:
        raise ValueError(f"no workflow replay proof declared for lane {lane}")
    return jobs


def _required(files: tuple[ParsedYamlFile, ...], config: ReplayConfig,
              selection: ReplaySelection, jobs: tuple[DeclaredReplayJob, ...]) -> tuple[ReplayJob, ...]:
    if not config.qualified:
        return tuple(job.proof for job in jobs)
    expanded = expand_selection(files, selection.workflow or config.workflow, selection.selected_job, qualified=True,
                                inputs=tuple(BoundInput(item.name, item.value) for item in selection.inputs))
    if expanded.problem:
        raise ValueError(expanded.problem)
    declarations = {job.source_job: job for job in jobs}
    if len(declarations) != len(jobs):
        raise ValueError("qualified replay has duplicate source-job declarations")
    if set(declarations) != {job.source_job for job in expanded.jobs}:
        raise ValueError("qualified replay declaration differs from the complete selected graph")
    return tuple(derive_checks(job, mode=config.mode, event=selection.event)
                 if declarations[job.source_job].derive_checks else
                 replace(declarations[job.source_job].proof, key=job.key, identity=job.identity)
                 for job in expanded.jobs)


def _full(config: ReplayConfig, files: tuple[ParsedYamlFile, ...]) -> ReplayPlan:
    if not config.qualified:
        raise ValueError("multi-selection full replay requires qualified execution identities")
    first = config.selections[0]
    workflow = first.workflow or config.workflow
    if any((item.workflow or config.workflow) != workflow or item.event != first.event or item.inputs != first.inputs
           for item in config.selections):
        raise ValueError("one full replay requires the same workflow, event and inputs for every selection")
    lanes = tuple(item.lane for item in config.selections)
    if len(set(lanes)) != len(lanes) or set(lanes) != {lane for job in config.jobs for lane in job.lanes}:
        raise ValueError("full replay selections must account for every declared proof lane exactly once")
    required: dict[tuple[JobIdentity, ...], ReplayJob] = {}
    for selection in config.selections:
        for job in _required(files, config, selection, _declared(config, selection.lane)):
            previous = required.get(job.identity)
            if previous is not None and previous != job:
                raise ValueError("shared replay execution has inconsistent check declarations")
            required[job.identity] = job
    return ReplayPlan(workflow, None, first.event, first.inputs, tuple(required.values()), lanes)


def build_plan(repo: Path, config: ReplayConfig, *, lane: str | None = None) -> ReplayPlan:
    files = tuple(load_workflows(repo)) if config.qualified else ()
    if lane is None and config.selections:
        return _full(config, files)
    jobs = _declared(config, lane)
    refs = {job.source_job for job in jobs}
    selected = next(iter(refs)).split(":", 1)[1] if lane is not None and len(refs) == 1 else None
    fallback = ReplaySelection(lane or "", selected, "pull_request", ())
    selection = next((item for item in config.selections if item.lane == lane), fallback)
    return ReplayPlan(selection.workflow or config.workflow, selection.selected_job, selection.event, selection.inputs,
                      _required(files, config, selection, jobs))
