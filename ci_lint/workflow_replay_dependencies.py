"""Bind a selected replay job to every statically resolvable dependency."""

from dataclasses import dataclass

from ci_lint.finding import Finding, Status
from ci_lint.workflow_replay_config import ReplayConfig
from ci_lint.workflow_scan import ParsedYamlFile, jobs_of
from ci_lint.yaml_io import YamlValue


@dataclass(frozen=True)
class DependencyProof:
    jobs: tuple[str, ...]
    problem: str | None = None


def _needs(job: dict[str, YamlValue]) -> tuple[str, ...]:
    raw = job.get("needs", [])
    values = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, list) or any(not isinstance(item, str) or not item or "${{" in item for item in values):
        raise ValueError("dependency expression is not a literal job list")
    return tuple(item for item in values if isinstance(item, str))


def dependency_closure(document: dict[str, YamlValue], selected: str) -> DependencyProof:
    jobs = jobs_of(document)
    seen: set[str] = set()
    active: set[str] = set()

    def visit(job_id: str) -> None:
        if job_id in active:
            raise ValueError("dependency graph contains a cycle")
        if job_id in seen:
            return
        if job_id not in jobs:
            raise ValueError(f"dependency job {job_id!r} does not exist")
        active.add(job_id)
        for dependency in _needs(jobs[job_id]):
            visit(dependency)
        active.remove(job_id)
        seen.add(job_id)

    try:
        visit(selected)
    except ValueError as exc:
        return DependencyProof((), str(exc))
    return DependencyProof(tuple(sorted(seen)))


def check_selection_dependencies(config: ReplayConfig, document: dict[str, YamlValue],
                                 files: tuple[ParsedYamlFile, ...] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    workflow = config.workflow.rsplit("/", 1)[-1]
    for selection in config.selections:
        proof = _selection_closure(config, document, selection.selected_job, files)
        if proof.problem:
            findings.append(Finding(rule="GATE-001", path=config.workflow, status=Status.NEEDS_REVIEW,
                                    message=f"replay selection {selection.lane}: {proof.problem}",
                                    fix="resolve the selected job's complete dependency graph"))
            continue
        declared = {job.source_job if files is not None else job.source_job.split(":", 1)[1]
                    for job in config.jobs if selection.lane in job.lanes
                    and (files is not None or job.source_job.startswith(workflow + ":"))}
        missing = set(proof.jobs) - declared
        extra = declared - set(proof.jobs)
        if missing or extra:
            findings.append(Finding(rule="GATE-001", path=config.workflow,
                                    message=f"replay dependency coverage differs for {selection.lane}: omitted={sorted(missing)}, unreachable={sorted(extra)}",
                                    fix="declare the selected job and every dependency for that lane"))
    return findings


def _selection_closure(config: ReplayConfig, document: dict[str, YamlValue], selected: str,
                       files: tuple[ParsedYamlFile, ...] | None) -> DependencyProof:
    if files is None:
        return dependency_closure(document, selected)
    # Lazy import keeps the literal-needs parser shared with the resolver.
    from ci_lint.workflow_replay_expansion import expand_selection

    proof = expand_selection(files, config.workflow, selected)
    return DependencyProof(tuple(job.source_job for job in proof.jobs), proof.problem)
