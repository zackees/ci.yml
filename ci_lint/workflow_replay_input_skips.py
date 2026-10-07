"""Finite exclusions from bound source inputs and earlier skipped producers."""

from collections import Counter

from ci_lint.workflow_replay_conditions import condition_excludes
from ci_lint.workflow_replay_inputs import BoundInput
from ci_lint.workflow_scan import steps_of
from ci_lint.yaml_io import YamlValue


def excluded_input_steps(job: dict[str, YamlValue], inputs: tuple[BoundInput, ...], *,
                         successful: bool = False, event: str | None = None) -> tuple[str, ...]:
    steps = steps_of(job)
    ids = Counter(step.get("id") for step in steps if isinstance(step.get("id"), str))
    producers: set[str] = set()
    names: list[str] = []
    for step in steps:
        name = step.get("name")
        if not isinstance(name, str) or not name.strip() or "${{" in name:
            continue
        if not condition_excludes(step.get("if"), inputs, producers, successful=successful, event=event):
            continue
        names.append(name)
        identity = step.get("id")
        if isinstance(identity, str) and ids[identity] == 1:
            producers.add(identity)
    return tuple(names)
