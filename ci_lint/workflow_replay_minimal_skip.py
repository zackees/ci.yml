"""Classify the finite source guards excluded from a minimal PR replay.

Classification alone is not permission to omit execution proof. A caller must
also require the producer's successful Main section and an explicit skipped
Main section for the classified step, on a successful minimal PR job.
"""

from dataclasses import dataclass

from ci_lint.workflow_scan import steps_of
from ci_lint.yaml_io import YamlValue


@dataclass(frozen=True)
class MinimalSkip:
    name: str
    producer: str
    failure_handler: bool = False


PROFILE_GUARDS = frozenset({
    "steps.mode.outputs.mode == 'full'",
    "steps.mode.outputs.mode != 'minimal'",
    "always() && steps.mode.outputs.mode == 'full'",
})
FAILURE_GUARD = "failure() && github.event_name == 'pull_request' && steps.mode.outputs.mode != 'full'"


def classify_minimal_skip(job: dict[str, YamlValue], name: str) -> MinimalSkip | None:
    """Unknown guards, ambiguous producers and general optional checks fail closed."""
    steps = steps_of(job)
    selected = [step for step in steps if step.get("name") == name]
    if len(selected) != 1:
        return None
    step = selected[0]
    expression = step.get("if")
    if not isinstance(expression, str):
        return None
    expression = expression.strip()
    if expression.startswith("${{") and expression.endswith("}}"):
        expression = expression[3:-2].strip()
    failure = expression == FAILURE_GUARD
    if expression not in PROFILE_GUARDS and not failure:
        return None
    handler = step.get("run")
    if failure and (not isinstance(handler, str) or handler.strip() != "gh run cancel ${{ github.run_id }}"
                    or "uses" in step):
        return None
    producers = [candidate for candidate in steps if candidate.get("id") == "mode"]
    if len(producers) != 1:
        return None
    producer = producers[0]
    producer_name = producer.get("name")
    command = producer.get("run")
    if (not isinstance(producer_name, str) or not producer_name.strip() or "${{" in producer_name
            or not isinstance(command, str) or not command.strip() or "uses" in producer
            or "if" in producer or steps.index(producer) >= steps.index(step)):
        return None
    return MinimalSkip(name, producer_name, failure)
