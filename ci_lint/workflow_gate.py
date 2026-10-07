"""Select a native workflow's required jobs through the existing source guards.

Selection outputs are same-run values from successful dependencies. Reuse and
skip outputs never excuse required coverage; the runtime gate owns their proof.
Unknown guards keep jobs required, and failures remain visible in every mode.
"""

import re
from pathlib import Path

from ci_lint.cargo_messages import JsonValue
from ci_lint.gate_trust import TRUSTED_OUTPUT
from ci_lint.local_gate import VERIFY_TOKEN
from ci_lint.rules.default_branch_skip import _REUSE_CHECK_RE
from ci_lint.workflow_replay_conditions import condition_excludes
from ci_lint.workflow_replay_dependencies import _needs, dependency_closure
from ci_lint.workflow_replay_identity import identity_part
from ci_lint.workflow_replay_outputs import ReplayOutput, valid_output_name
from ci_lint.workflow_scan import jobs_of, load_workflows, steps_of
from ci_lint.yaml_io import YamlValue


def _decision_output(name: str) -> bool:
    return name.startswith("skip_") or name in (TRUSTED_OUTPUT, "reuse", "would_reuse")


def _decision_steps(job: dict[str, YamlValue]) -> tuple[str, ...]:
    return tuple(
        step["id"]
        for step in steps_of(job)
        if isinstance(step.get("id"), str)
        and isinstance(step.get("run"), str)
        and (VERIFY_TOKEN in step["run"] or _REUSE_CHECK_RE.search(step["run"]))
    )


def _selection_output(name: str, declaration: YamlValue, decisions: tuple[str, ...]) -> bool:
    if not isinstance(declaration, str) or _decision_output(name):
        return False
    references = re.finditer(
        r"\bsteps(?:\.(?P<dot>[A-Za-z_][A-Za-z0-9_-]*)|\[\s*['\"](?P<bracket>[A-Za-z_][A-Za-z0-9_-]*)['\"]\s*\])",
        declaration,
    )
    if any((item["dot"] or item["bracket"]) in decisions for item in references):
        return False
    # Also retain reserved decisions when their origin is not statically known.
    return not any(_decision_output(item) for item in re.findall(r"\boutputs\.([A-Za-z_][A-Za-z0-9_-]*)", declaration))


def _outputs(needs: dict[str, JsonValue], jobs: dict[str, YamlValue]) -> tuple[ReplayOutput, ...]:
    outputs: list[ReplayOutput] = []
    for producer, entry in needs.items():
        if not isinstance(entry, dict) or entry.get("result") != "success":
            continue
        job = jobs.get(producer)
        if not isinstance(job, dict) or "strategy" in job:
            continue  # A matrix caller's aggregate outputs have no unique producer.
        declared = job.get("outputs") if isinstance(job, dict) else None
        values = entry.get("outputs")
        if not isinstance(declared, dict) or not isinstance(values, dict):
            continue
        if len(values) > 256:
            raise ValueError("workflow gate producer outputs exceed the bounded contract")
        decisions = _decision_steps(job)
        for name, value in values.items():
            if (
                not _selection_output(name, declared.get(name), decisions)
                or not valid_output_name(name)
                or not isinstance(value, str)
            ):
                continue
            if len(value.encode("utf-8")) > 65536:
                raise ValueError("workflow gate producer output exceeds 64 KiB")
            outputs.append(ReplayOutput((identity_part(producer),), name, value, 0))
    return tuple(outputs)


def _source(repo: Path, workflow: str, gate: str) -> dict[str, YamlValue]:
    """Load a bounded source graph with a real, nonempty aggregation job."""
    if re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", workflow) is None:
        raise ValueError("workflow gate requires a literal workflow basename")
    files = [item for item in load_workflows(repo) if item.path == f".github/workflows/{workflow}"]
    if len(files) != 1 or not isinstance(files[0].document, dict):
        raise ValueError("workflow gate source is missing or unparsed")
    jobs = jobs_of(files[0].document)
    if len(jobs) > 512:
        raise ValueError("workflow gate exceeds the bounded graph limit")
    selected = jobs.get(gate)
    if selected is None:
        raise ValueError("workflow gate job does not exist")
    dependencies = _needs(selected)
    if not dependencies or len(set(dependencies)) != len(dependencies):
        raise ValueError("workflow gate needs a nonempty unique dependency list")
    closure = dependency_closure(files[0].document, gate)
    if closure.problem:
        raise ValueError(closure.problem)
    return files[0].document


def build_plan(
    repo: Path, workflow: str, gate: str, needs: dict[str, JsonValue], *, event: str
) -> dict[str, JsonValue]:
    """A gate plan from its declared dependencies and finite source conditions."""
    if not event:
        raise ValueError("workflow gate needs a known event name")
    jobs = jobs_of(_source(repo, workflow, gate))
    dependencies = _needs(jobs[gate])
    outputs = _outputs(needs, jobs)
    exclusions: dict[str, bool] = {}

    def excluded(job_id: str) -> bool:
        if job_id not in exclusions:
            job = jobs[job_id]
            dependencies = _needs(job)
            exclusions[job_id] = condition_excludes(
                job.get("if"), (), set(), event=event, outputs=outputs, dependencies=dependencies
            )
            # With no explicit override, GitHub's implicit success() excludes
            # jobs downstream of a source-excluded dependency. A raw skipped
            # result alone never establishes that dependency exclusion.
            if "if" not in job:
                exclusions[job_id] = exclusions[job_id] or any(excluded(item) for item in dependencies)
        return exclusions[job_id]

    required: list[str] = []
    for job_id in dependencies:
        entry = needs.get(job_id)
        failed = isinstance(entry, dict) and entry.get("result") in ("failure", "cancelled")
        if failed or not excluded(job_id):
            required.append(job_id)
    if not required:
        raise ValueError("workflow gate source selected no required job")
    return {"required_jobs": required, "mergeable": True}
