"""Resolve one replay invocation into complete source-bound execution proof."""

from dataclasses import dataclass, replace
from pathlib import Path

from ci_lint.workflow_replay import ReplayInput, ReplayJob
from ci_lint.workflow_replay_checks import derive_checks
from ci_lint.workflow_replay_conditions import condition_output_references
from ci_lint.workflow_replay_dependencies import _needs
from ci_lint.workflow_replay_config import DeclaredReplayJob, ReplayConfig, ReplaySelection
from ci_lint.workflow_replay_expansion import ExpandedJob, OutputProver, expand_selection
from ci_lint.workflow_replay_identity import JobIdentity
from ci_lint.workflow_replay_inputs import BoundInput
from ci_lint.workflow_replay_outputs import ReplayOutput, PendingOutput, dependency_output
from ci_lint.workflow_scan import ParsedYamlFile, load_workflows, as_dict, steps_of


@dataclass(frozen=True)
class ReplayPlan:
    workflow: str
    selected: str | None
    event: str
    inputs: tuple[ReplayInput, ...]
    required: tuple[ReplayJob, ...]
    lanes: tuple[str, ...] = ()
    deferred_outputs: bool = False


@dataclass(frozen=True)
class ReplayRequirements:
    jobs: tuple[ReplayJob, ...]
    pending: bool = False


def _declared(config: ReplayConfig, lane: str | None) -> tuple[DeclaredReplayJob, ...]:
    jobs = tuple(job for job in config.jobs if lane is None or lane in job.lanes)
    if not jobs:
        raise ValueError(f"no workflow replay proof declared for lane {lane}")
    return jobs



def declared_check(declared: DeclaredReplayJob, expanded: ExpandedJob, *, mode: str, event: str) -> ReplayJob:
    return (derive_checks(expanded, mode=mode, event=event) if declared.derive_checks else
            replace(declared.proof, key=expanded.key, identity=expanded.identity))


def _pending_guards(expanded: ExpandedJob) -> bool:
    job = as_dict(expanded.job)
    dependencies = _needs(job)
    conditions = (job.get("if"), *(step.get("if") for step in steps_of(job)))
    pending = False
    for condition in conditions:
        for reference in condition_output_references(condition):
            if reference.producer not in dependencies:
                continue  # An unknown context cannot excuse a required check.
            try:
                dependency_output(expanded.outputs, expanded.identity[:-1], reference.producer, reference.name)
            except PendingOutput:
                pending = True
    return pending


def _required(files: tuple[ParsedYamlFile, ...], config: ReplayConfig,
              selection: ReplaySelection, jobs: tuple[DeclaredReplayJob, ...], *,
              defer_outputs: bool = False, prove_outputs: OutputProver | None = None) -> ReplayRequirements:
    if not config.qualified:
        return ReplayRequirements(tuple(job.proof for job in jobs))
    declarations = {job.source_job: job for job in jobs}
    if len(declarations) != len(jobs):
        raise ValueError("qualified replay has duplicate source-job declarations")
    pending_guards = False

    def preflight(expanded: ExpandedJob) -> tuple[ReplayOutput, ...]:
        nonlocal pending_guards
        declared = declarations.get(expanded.source_job)
        if declared is None:
            raise ValueError("qualified replay producer has no source declaration")
        declared_check(declared, expanded, mode=config.mode, event=selection.event)
        pending_guards = pending_guards or (declared.derive_checks and _pending_guards(expanded))
        return ()

    expanded = expand_selection(files, selection.workflow or config.workflow, selection.selected_job, qualified=True,
                                inputs=tuple(BoundInput(item.name, item.value) for item in selection.inputs),
                                prove_outputs=prove_outputs or (preflight if defer_outputs else None),
                                event=selection.event)
    if expanded.pending and defer_outputs:
        return ReplayRequirements((), pending=True)
    if expanded.problem:
        raise ValueError(expanded.problem)
    if set(declarations) != {job.source_job for job in expanded.jobs}:
        raise ValueError("qualified replay declaration differs from the complete selected graph")
    proofs = tuple(declared_check(declarations[job.source_job], job, mode=config.mode, event=selection.event)
                   for job in expanded.jobs)
    if all(proof.excluded for proof in proofs):
        raise ValueError("qualified replay selection has no required executed job")
    return ReplayRequirements(proofs, pending=pending_guards)


def _full(config: ReplayConfig, files: tuple[ParsedYamlFile, ...], *,
          defer_outputs: bool, prove_outputs: OutputProver | None) -> ReplayPlan:
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
    pending = False
    for selection in config.selections:
        requirements = _required(files, config, selection, _declared(config, selection.lane),
                                 defer_outputs=defer_outputs, prove_outputs=prove_outputs)
        pending = pending or requirements.pending
        for job in requirements.jobs:
            previous = required.get(job.identity)
            if previous is not None and previous != job:
                raise ValueError("shared replay execution has inconsistent check declarations")
            required[job.identity] = job
    return ReplayPlan(workflow, None, first.event, first.inputs, tuple(required.values()), lanes, pending)


def build_plan(repo: Path, config: ReplayConfig, *, lane: str | None = None,
               defer_outputs: bool = False, prove_outputs: OutputProver | None = None,
               files: tuple[ParsedYamlFile, ...] | None = None) -> ReplayPlan:
    if files is None:
        files = tuple(load_workflows(repo)) if config.qualified else ()
    if lane is None and config.selections:
        return _full(config, files, defer_outputs=defer_outputs, prove_outputs=prove_outputs)
    jobs = _declared(config, lane)
    refs = {job.source_job for job in jobs}
    selected = next(iter(refs)).split(":", 1)[1] if lane is not None and len(refs) == 1 else None
    fallback = ReplaySelection(lane or "", selected, "pull_request", ())
    selection = next((item for item in config.selections if item.lane == lane), fallback)
    requirements = _required(files, config, selection, jobs, defer_outputs=defer_outputs, prove_outputs=prove_outputs)
    return ReplayPlan(selection.workflow or config.workflow, selection.selected_job, selection.event, selection.inputs,
                      requirements.jobs, deferred_outputs=requirements.pending)
