"""One source-derived check contract shared by static and execution proof."""

from ci_lint.workflow_replay import ReplayJob
from ci_lint.workflow_replay_conditions import condition_excludes
from ci_lint.workflow_replay_cache_save import approved_cache_save, approved_pr_cache_save
from ci_lint.workflow_replay_expansion import ExpandedJob
from ci_lint.workflow_replay_input_skips import excluded_input_steps
from ci_lint.workflow_replay_inputs import bound_name
from ci_lint.workflow_replay_minimal_skip import classify_minimal_skip
from ci_lint.workflow_scan import as_dict, steps_of
from ci_lint.yaml_io import YamlValue


def _named_job(expanded: ExpandedJob) -> dict[str, YamlValue]:
    job = as_dict(expanded.job)
    steps: list[YamlValue] = []
    for step in steps_of(job):
        if "uses" not in step and "run" not in step:
            continue
        steps.append({**step, "name": bound_name(step.get("name"), expanded.inputs)})
    return {**job, "steps": steps}


def derive_checks(expanded: ExpandedJob, *, mode: str, event: str) -> ReplayJob:
    """Require all source steps, with only existing finite skip classifications.

    Every exclusion still requires exactly one explicit skipped Main section;
    every other step requires successful execution. Names must be unique.
    """
    if condition_excludes(as_dict(expanded.job).get("if"), expanded.inputs, set(), event=event):
        return ReplayJob(expanded.key, (), identity=expanded.identity, excluded=True)
    job = _named_job(expanded)
    names = tuple(str(step["name"]) for step in steps_of(job))
    if not names or len(set(names)) != len(names):
        raise ValueError("source-derived replay requires distinct named executable steps")
    inputs = excluded_input_steps(job, expanded.inputs, successful=True, event=event)
    minimal = tuple(skip for name in names
                    if mode == "minimal" and event == "pull_request"
                    and (skip := classify_minimal_skip(job, name)) is not None and name not in inputs)
    producers = {skip.producer for skip in minimal}
    if len(producers) > 1:
        raise ValueError("source-derived minimal exclusions have ambiguous producers")
    minimal_names = tuple(skip.name for skip in minimal)
    pr_saves = tuple(name for name in names if event == "pull_request" and approved_pr_cache_save(job, name)
                     and name not in (*inputs, *minimal_names))
    cache_saves = tuple(name for name in names if approved_cache_save(job, name)
                        and name not in (*inputs, *minimal_names, *pr_saves))
    return ReplayJob(expanded.key, names, cache_saves, minimal_names, next(iter(producers), ""),
                     pr_saves, inputs, expanded.identity)
