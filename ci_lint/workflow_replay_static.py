"""Conservative binding of replay check names to repository workflow steps."""

import re
from pathlib import Path

from ci_lint.finding import Finding, Status
from ci_lint.workflow_replay_dependencies import check_selection_dependencies
from ci_lint.workflow_replay_config import DeclaredReplayJob, ReplayConfig
from ci_lint.workflow_scan import as_dict, jobs_of, load_workflows, steps_of
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


def _check_job(declared: DeclaredReplayJob, job: dict[str, YamlValue],
               path: str, workflow_defaults: YamlValue = None) -> list[Finding]:
    findings: list[Finding] = []
    if "uses" in job or "strategy" in job:
        return [Finding(rule="GATE-001", path=path, status=Status.NEEDS_REVIEW,
                        message=f"replay job {declared.source_job} has reusable or matrix coverage",
                        fix="prove expanded job and step coverage before attesting this replay")]
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
                    job: dict[str, YamlValue], path: str) -> list[Finding]:
    workflow, job_id = declared.source_job.split(":", 1)
    workflow_name = document.get("name", workflow)
    job_name = job.get("name", job_id)
    if any(not isinstance(name, str) or not name.strip() or "${{" in name
           for name in (workflow_name, job_name)):
        return [Finding(rule="GATE-001", path=path, status=Status.NEEDS_REVIEW,
                        message="replay execution key depends on a nonliteral workflow or job name",
                        fix="resolve the execution identity before proving replay coverage")]
    expected = f"{workflow_name}/{job_name}"
    if declared.proof.key != expected:
        return [Finding(rule="GATE-001", path=path,
                        message=f"replay execution key {declared.proof.key!r} differs from workflow identity {expected!r}",
                        fix="declare the exact workflow-name/job-name execution key")]
    return []


def check_replay_static(config: ReplayConfig, repo: Path) -> list[Finding]:
    workflows = {item.path: item for item in load_workflows(repo)}
    findings: list[Finding] = []
    entry = workflows.get(config.workflow)
    if entry is not None and entry.document is not None:
        findings.extend(check_selection_dependencies(config, as_dict(entry.document)))
    for declared in config.jobs:
        workflow, job_id = declared.source_job.split(":", 1)
        path = f".github/workflows/{workflow}"
        if path != config.workflow:
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
            findings.extend(_check_identity(declared, document, job, path))
        findings.extend(_check_job(declared, job, path, document.get("defaults")))
    return findings
