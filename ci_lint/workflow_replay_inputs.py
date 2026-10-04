"""Bind literal string workflow-call inputs without evaluating expressions."""

import re
from dataclasses import dataclass

from ci_lint.workflow_scan import as_dict, get_on_section
from ci_lint.yaml_io import YamlValue


@dataclass(frozen=True)
class BoundInput:
    name: str
    value: str


def input_value(value: YamlValue, inputs: tuple[BoundInput, ...]) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*\$\{\{\s*inputs\.([A-Za-z0-9_-]+)\s*\}\}\s*", value)
    if match:
        return next((item.value for item in inputs if item.name == match[1]), None)
    return value if "${{" not in value else None


def bind_call_inputs(document: dict[str, YamlValue], caller: dict[str, YamlValue],
                     parent: tuple[BoundInput, ...]) -> tuple[BoundInput, ...]:
    declarations = as_dict(as_dict(get_on_section(document).get("workflow_call")).get("inputs"))
    supplied = as_dict(caller.get("with"))
    if set(supplied) - set(declarations):
        raise ValueError("reusable call supplies undeclared inputs")
    result: list[BoundInput] = []
    for name, raw in declarations.items():
        descriptor = as_dict(raw)
        if descriptor.get("required") is True and name not in supplied:
            raise ValueError(f"reusable call lacks required input {name}")
        if descriptor.get("type") != "string":
            continue
        value = input_value(supplied[name], parent) if name in supplied else input_value(descriptor.get("default", ""), ())
        if value is not None:
            result.append(BoundInput(name, value))
    return tuple(result)


def bound_name(value: YamlValue, inputs: tuple[BoundInput, ...]) -> str:
    if not isinstance(value, str):
        raise ValueError("execution identity has a nonliteral workflow or job name")
    def substitute(match: re.Match[str]) -> str:
        found = next((item.value for item in inputs if item.name == match[1]), None)
        if found is None:
            raise ValueError(f"execution identity depends on unknown input {match[1]}")
        return found
    resolved = re.sub(r"\$\{\{\s*inputs\.([A-Za-z0-9_-]+)\s*\}\}", substitute, value)
    if not resolved.strip() or "${{" in resolved:
        raise ValueError("execution identity has a nonliteral workflow or job name")
    return resolved
