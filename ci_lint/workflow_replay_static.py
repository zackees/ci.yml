"""Conservative binding of replay check names to repository workflow steps."""

import re
from pathlib import Path

from ci_lint.finding import Finding, Status
from ci_lint.workflow_replay_cache_save import approved_cache_save, approved_pr_cache_save
from ci_lint.workflow_replay_minimal_skip import classify_minimal_skip
from ci_lint.workflow_replay_dependencies import check_selection_dependencies
from ci_lint.workflow_replay_config import DeclaredReplayJob, ReplayConfig
from ci_lint.workflow_replay_expansion import ExpandedJob, expand_selection
from ci_lint.workflow_replay_input_skips import excluded_input_steps
from ci_lint.workflow_scan import ParsedYamlFile, as_dict, jobs_of, load_workflows, steps_of
from ci_lint.yaml_io import YamlValue


def _informational_names(job: dict[str, YamlValue], workflow_defaults: YamlValue) -> tuple[str, ...]:
    # Only a single literal echo in an ordinary shell; never a general shell
    # parser or an owner-declared waiver. Custom/default shells remain checks.
    if workflow_defaults is not None or "defaults" in job:
        return ()
    names: list[str] = []
    for step in steps_of(job):
        run = step.get("run")
        name = step.get("name")
        if (isinstance(run, str) and isinstance(name, str) and "uses" not in step
                and step.get("shell") in (None, "bash", "sh")
                and re.fullmatch(r'echo "[A-Za-z0-9 .,():/_+\-]+"', run.strip())):
            names.append(name)
    return tuple(names)


def _check_minimal_skips(declared: DeclaredReplayJob, job: dict[str, YamlValue],
                         path: str, mode: str) -> list[Finding]:
    findings: list[Finding] = []
    for name in declared.proof.minimal_skip_steps:
        classified = classify_minimal_skip(job, name)
        if mode != "minimal" or classified is None or classified.producer != declared.proof.minimal_mode_step:
            findings.append(Finding(rule="GATE-001", path=path,
                                    message=f"minimal exclusion has no matching source guard and producer: {name}",
                                    fix="declare every selected check; unknown guards cannot waive proof"))
    return findings


def _check_cache_saves(declared: DeclaredReplayJob, job: dict[str, YamlValue],
                       path: str) -> list[Finding]:
    findings: list[Finding] = []
    for name in declared.proof.cache_save_steps:
        if not approved_cache_save(job, name):
            findings.append(Finding(rule="GATE-001", path=path,
                                    message=f"cache-save skip is not a matching exact-hit guarded cache save: {name}",
                                    fix="do not waive validation steps; bind the save to its earlier matching restore"))
    for name in declared.proof.pr_cache_save_steps:
        if not approved_pr_cache_save(job, name):
            findings.append(Finding(rule="GATE-001", path=path,
                                    message=f"PR-only cache save has no recognized non-PR source guard: {name}",
                                    fix="only official cache-save actions excluded by a finite event/ref guard may skip"))
    return findings


def _check_job(declared: DeclaredReplayJob, job: dict[str, YamlValue],
               path: str, workflow_defaults: YamlValue = None, mode: str = "minimal") -> list[Finding]:
    findings = _check_minimal_skips(declared, job, path, mode)
    if "uses" in job or "strategy" in job:
        return [Finding(rule="GATE-001", path=path, status=Status.NEEDS_REVIEW,
                        message=f"replay job {declared.source_job} has reusable or matrix coverage",
                        fix="prove expanded job and step coverage before attesting this replay")]
    findings.extend(_check_cache_saves(declared, job, path))
    names: list[str] = []
    for index, step in enumerate(steps_of(job)):
        if "run" not in step and "uses" not in step:
            continue
        name = step.get("name")
        if not isinstance(name, str) or not name.strip() or "${{" in name:
            findings.append(Finding(rule="GATE-001", path=path, status=Status.NEEDS_REVIEW,
                                    message=f"replay step {index + 1} has no literal execution name",
                                    fix="give each executable step a unique literal name"))
        else:
            names.append(name)
    if len(set(names)) != len(names):
        findings.append(Finding(rule="GATE-001", path=path,
                                message="replay job has ambiguous duplicate step names",
                                fix="give each executable step a unique name"))
    missing = set(names) - set(declared.proof.steps) - set(_informational_names(job, workflow_defaults))
    extra = set(declared.proof.steps) - set(names)
    if missing or extra:
        findings.append(Finding(rule="GATE-001", path=path,
                                message=f"replay check coverage differs: omitted={sorted(missing)}, unknown={sorted(extra)}",
                                fix="declare every executable step exactly; setup-steps does not waive replay proof"))
    return findings


def _check_identity(declared: DeclaredReplayJob, document: dict[str, YamlValue],
                    job: dict[str, YamlValue], path: str,
                    expanded_keys: tuple[str, ...] = ()) -> list[Finding]:
    workflow, job_id = declared.source_job.split(":", 1)
    workflow_name = document.get("name", workflow)
    job_name = job.get("name", job_id)
    identity_names = (workflow_name,) if len(expanded_keys) == 1 else (workflow_name, job_name)
    if any(not isinstance(name, str) or not name.strip() or "${{" in name for name in identity_names):
        return [Finding(rule="GATE-001", path=path, status=Status.NEEDS_REVIEW,
                        message="replay execution key depends on a nonliteral workflow or job name",
                        fix="resolve the execution identity before proving replay coverage")]
    expected = expanded_keys[0] if len(expanded_keys) == 1 else f"{workflow_name}/{job_name}"
    if declared.proof.key != expected:
        return [Finding(rule="GATE-001", path=path,
                        message=f"replay execution key {declared.proof.key!r} differs from workflow identity {expected!r}",
                        fix="declare the exact workflow-name/job-name execution key")]
    return []


def _expanded_jobs(config: ReplayConfig, declared: DeclaredReplayJob,
                   files: tuple[ParsedYamlFile, ...]) -> tuple[ExpandedJob, ...]:
    jobs: list[ExpandedJob] = []
    for selection in config.selections:
        if selection.lane not in declared.lanes:
            continue
        proof = expand_selection(files, selection.workflow or config.workflow, selection.selected_job)
        jobs.extend(job for job in proof.jobs if job.source_job == declared.source_job)
    return tuple(jobs)


def _check_input_skips(declared: DeclaredReplayJob, job: dict[str, YamlValue],
                       expanded: tuple[ExpandedJob, ...], path: str) -> list[Finding]:
    if not declared.proof.input_skip_steps:
        return []
    wanted = set(declared.proof.input_skip_steps)
    if not expanded or any(not wanted.issubset(excluded_input_steps(job, item.inputs)) for item in expanded):
        return [Finding(rule="GATE-001", path=path,
                        message="input exclusions are not proven by every selected caller binding",
                        fix="require literal empty inputs and finite source guards; unknown expressions cannot waive proof")]
    return []


def _check_pr_selections(config: ReplayConfig) -> list[Finding]:
    findings: list[Finding] = []
    for declared in config.jobs:
        if not declared.proof.pr_cache_save_steps:
            continue
        for selection in config.selections:
            if selection.lane in declared.lanes and selection.event != "pull_request":
                findings.append(Finding(rule="GATE-001", path=config.workflow,
                                        message=f"PR-only cache saves are declared for non-PR lane {selection.lane}",
                                        fix="require normal execution proof on non-PR selections"))
    return findings


def check_replay_static(config: ReplayConfig, repo: Path) -> list[Finding]:
    files = tuple(load_workflows(repo))
    workflows = {item.path: item for item in files}
    findings: list[Finding] = []
    entry = workflows.get(config.workflow)
    document = as_dict(entry.document) if entry is not None else {}
    findings.extend(check_selection_dependencies(config, document, files))
    findings.extend(_check_pr_selections(config))
    for declared in config.jobs:
        workflow, job_id = declared.source_job.split(":", 1)
        path = f".github/workflows/{workflow}"
        expanded = _expanded_jobs(config, declared, files)
        keys = tuple(sorted({job.key for job in expanded}))
        if path != config.workflow and len(keys) != 1:
            findings.append(Finding(rule="GATE-001", path=path, status=Status.NEEDS_REVIEW,
                                    message="replay job belongs to another workflow; entrypoint reachability is unproven",
                                    fix="prove reusable-workflow expansion and execution coverage"))
        parsed = workflows.get(path)
        if parsed is None:
            findings.append(Finding(rule="GATE-001", path=path,
                                    message="declared replay workflow does not exist",
                                    fix="declare an existing workflow"))
            continue
        if parsed.document is None:
            findings.append(Finding(rule="GATE-001", path=path, status=Status.NEEDS_REVIEW,
                                    message="cannot parse replay workflow",
                                    fix="resolve workflow parsing before proving coverage"))
            continue
        document = as_dict(parsed.document)
        job = jobs_of(document).get(job_id)
        if job is None:
            findings.append(Finding(rule="GATE-001", path=path,
                                    message=f"declared replay job {job_id} does not exist",
                                    fix="declare an existing workflow job"))
            continue
        if "uses" not in job and "strategy" not in job:
            findings.extend(_check_identity(declared, document, job, path, keys))
        findings.extend(_check_job(declared, job, path, document.get("defaults"), config.mode))
        findings.extend(_check_input_skips(declared, job, expanded, path))
    return findings
