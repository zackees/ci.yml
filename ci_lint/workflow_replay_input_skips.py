"""Finite exclusions from bound empty inputs and earlier skipped producers."""

import re
from collections import Counter

from ci_lint.workflow_replay_inputs import BoundInput
from ci_lint.workflow_scan import steps_of
from ci_lint.yaml_io import YamlValue


def _excluded(expression: YamlValue, inputs: tuple[BoundInput, ...], producers: set[str]) -> bool:
    if not isinstance(expression, str):
        return False
    expression = expression.strip()
    if expression.startswith("${{") and expression.endswith("}}"):
        expression = expression[3:-2].strip()
    root = re.fullmatch(r"inputs\.([A-Za-z0-9_-]+) != ''", expression)
    if root:
        return any(item.name == root[1] and item.value == "" for item in inputs)
    dependent = re.fullmatch(r"steps\.([A-Za-z0-9_-]+)\.outcome == 'success'", expression)
    return dependent is not None and dependent[1] in producers


def excluded_input_steps(job: dict[str, YamlValue], inputs: tuple[BoundInput, ...]) -> tuple[str, ...]:
    steps = steps_of(job)
    ids = Counter(step.get("id") for step in steps if isinstance(step.get("id"), str))
    producers: set[str] = set()
    names: list[str] = []
    for step in steps:
        name = step.get("name")
        if not isinstance(name, str) or not name.strip() or "${{" in name:
            continue
        if not _excluded(step.get("if"), inputs, producers):
            continue
        names.append(name)
        identity = step.get("id")
        if isinstance(identity, str) and ids[identity] == 1:
            producers.add(identity)
    return tuple(names)
