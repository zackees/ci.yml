"""Resolve literal local reusable calls into concrete execution identities."""

import re
from dataclasses import dataclass

from ci_lint.workflow_replay_dependencies import _needs
from ci_lint.workflow_replay_inputs import BoundInput, bind_call_inputs, bound_name
from ci_lint.workflow_scan import ParsedYamlFile, as_dict, get_on_section, jobs_of
from ci_lint.yaml_io import YamlValue


@dataclass(frozen=True)
class ExpandedJob:
    source_job: str
    key: str
    path: str
    document: YamlValue
    job: YamlValue
    inputs: tuple[BoundInput, ...] = ()


@dataclass(frozen=True)
class ReplayExpansion:
    jobs: tuple[ExpandedJob, ...]
    problem: str | None = None


def _literal_name(value: YamlValue) -> str:
    if not isinstance(value, str) or not value.strip() or "${{" in value:
        raise ValueError("execution identity has a nonliteral workflow or job name")
    return value


def _callee_path(value: YamlValue) -> str:
    if not isinstance(value, str) or re.fullmatch(r"\./\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml", value) is None:
        raise ValueError("reusable workflow is not a literal repository-local path")
    return value[2:]


def _document(files: tuple[ParsedYamlFile, ...], path: str) -> dict[str, YamlValue]:
    matching = [item for item in files if item.path == path]
    if len(matching) != 1 or not isinstance(matching[0].document, dict):
        raise ValueError(f"workflow {path} is missing, ambiguous or unparsed")
    return matching[0].document


def _resolve_job(files: tuple[ParsedYamlFile, ...], path: str, job_id: str, prefix: str,
                 inputs: tuple[BoundInput, ...]) -> ExpandedJob:
    document = _document(files, path)
    job = jobs_of(document).get(job_id)
    if job is None:
        raise ValueError(f"dependency job {path}:{job_id} does not exist")
    if "strategy" in job:
        raise ValueError("matrix expansion is not statically proven")
    basename = path.rsplit("/", 1)[-1]
    key = prefix + _literal_name(document.get("name", basename)) + "/" + bound_name(job.get("name", job_id), inputs)
    return ExpandedJob(f"{basename}:{job_id}", key, path, document, job, inputs)


@dataclass(frozen=True)
class _ExpansionState:
    files: tuple[ParsedYamlFile, ...]
    jobs: list[ExpandedJob]
    visited: set[str]
    active: set[str]


def _record(state: _ExpansionState, resolved: ExpandedJob) -> None:
    previous = next((job for job in state.jobs if job.source_job == resolved.source_job), None)
    if previous is not None:
        if previous.key != resolved.key:
            raise ValueError(f"reusable job {resolved.source_job} has multiple execution identities")
        return
    state.jobs.append(resolved)


def _begin(state: _ExpansionState, identity: str) -> bool:
    if identity in state.active:
        raise ValueError("dependency graph contains a cycle")
    if identity in state.visited:
        return False
    if len(state.visited) + len(state.active) >= 512:
        raise ValueError("reusable expansion exceeds the bounded graph limit")
    state.active.add(identity)
    return True


def _visit(state: _ExpansionState, current: str, job_id: str,
           prefix: str, ancestry: tuple[str, ...], inputs: tuple[BoundInput, ...] = ()) -> None:
    identity = f"{prefix}|{current}:{job_id}"
    if not _begin(state, identity):
        return
    if len(ancestry) > 8:
        raise ValueError("reusable expansion exceeds the bounded graph limit")
    resolved = _resolve_job(state.files, current, job_id, prefix, inputs)
    job = as_dict(resolved.job)
    for dependency in _needs(job):
        _visit(state, current, dependency, prefix, ancestry, inputs)
    if "uses" in job:
        _called(state, current, job, job_id, prefix, ancestry, inputs)
    else:
        _record(state, resolved)
    state.active.remove(identity)
    state.visited.add(identity)


def _called(state: _ExpansionState, current: str, job: dict[str, YamlValue],
            job_id: str, prefix: str, ancestry: tuple[str, ...], inputs: tuple[BoundInput, ...]) -> None:
    callee = _callee_path(job.get("uses"))
    if callee in ancestry or callee == current:
        raise ValueError("reusable workflow graph contains a cycle")
    document = _document(state.files, callee)
    jobs = jobs_of(document)
    if "workflow_call" not in get_on_section(document) or not jobs:
        raise ValueError("called workflow has no workflow_call contract or executable jobs")
    bound = bind_call_inputs(document, job, inputs)
    for child in jobs:
        _visit(state, callee, child, prefix + job_id + "/", ancestry + (current,), bound)


def expand_selection(files: tuple[ParsedYamlFile, ...], path: str, selected: str | None) -> ReplayExpansion:
    """Exclude virtual callers; require all their concrete jobs and prerequisites.

    A source-job declaration cannot disambiguate two invocations of the same
    called job, so repeated calls with distinct prefixes remain unproven.
    """
    state = _ExpansionState(files, [], set(), set())
    try:
        roots = (selected,) if selected is not None else tuple(jobs_of(_document(files, path)))
        if not roots:
            raise ValueError("workflow has no executable jobs")
        for root in roots:
            _visit(state, path, root, "", ())
    except ValueError as exc:
        return ReplayExpansion((), str(exc))
    return ReplayExpansion(tuple(sorted(state.jobs, key=lambda job: job.source_job)))
