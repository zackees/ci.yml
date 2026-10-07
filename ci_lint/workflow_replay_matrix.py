"""One bounded matrix expander for literals and proved dependency outputs."""

import itertools
import json
import math
import re

from ci_lint.workflow_replay_identity import JobIdentity, matrix_json
from ci_lint.workflow_replay_outputs import ReplayOutput, dependency_output, unique_json_object
from ci_lint.workflow_replay_dependencies import _needs
from ci_lint.yaml_io import YamlValue

MAX_MATRIX = 256


def _literal(value: YamlValue, depth: int = 0) -> None:
    if depth > 32 or (isinstance(value, float) and not math.isfinite(value)):
        raise ValueError("matrix has excessive depth or nonfinite values")
    if isinstance(value, str) and "${{" in value:
        raise ValueError("dynamic matrix expansion is not statically proven")
    if isinstance(value, dict):
        for child in value.values():
            _literal(child, depth + 1)
    if isinstance(value, list):
        for child in value:
            _literal(child, depth + 1)


def _entries(matrix: dict[str, YamlValue], name: str) -> list[dict[str, YamlValue]]:
    raw = matrix.get(name, [])
    if not isinstance(raw, list) or len(raw) > MAX_MATRIX or any(not isinstance(item, dict) for item in raw):
        raise ValueError(f"matrix {name} must be literal objects")
    return [item for item in raw if isinstance(item, dict)]


def _matches(left: dict[str, YamlValue], right: dict[str, YamlValue], keys: tuple[str, ...]) -> bool:
    return all(json.dumps(left[key], sort_keys=True) == json.dumps(right[key], sort_keys=True)
               for key in keys if key in left and key in right)


def _product(matrix: dict[str, YamlValue], axes: tuple[str, ...]) -> list[dict[str, YamlValue]]:
    values: list[list[YamlValue]] = []
    for axis in axes:
        raw = matrix[axis]
        if not isinstance(raw, list) or not raw:
            raise ValueError("matrix axes must be nonempty literal arrays")
        values.append(raw)
    if math.prod(len(items) for items in values) > MAX_MATRIX:
        raise ValueError("matrix expansion exceeds the bounded graph limit")
    return [dict(zip(axes, items, strict=True)) for items in itertools.product(*values)] if axes else []


def _apply_includes(rows: list[dict[str, YamlValue]], includes: list[dict[str, YamlValue]],
                     axes: tuple[str, ...]) -> None:
    extras: list[dict[str, YamlValue]] = []
    for include in includes:
        matched = [row for row in rows if _matches(row, include, axes)]
        for row in matched:
            row.update(include)
        if not matched:
            extras.append(include)
    rows.extend(extras)



def _resolved(value: YamlValue, job: dict[str, YamlValue], outputs: tuple[ReplayOutput, ...],
              scope: tuple[JobIdentity, ...]) -> YamlValue:
    if not isinstance(value, str) or "${{" not in value:
        return value
    match = re.fullmatch(
        r"\s*\$\{\{\s*fromJSON\(\s*needs\.([A-Za-z_][A-Za-z0-9_-]*)"
        r"\.outputs\.([A-Za-z_][A-Za-z0-9_-]*)\s*\)\s*\}\}\s*", value,
        flags=re.IGNORECASE)
    if match is None or match[1] not in _needs(job):
        raise ValueError("dynamic matrix requires a declared dependency output")
    output = dependency_output(outputs, scope, match[1], match[2])
    if len(output.value.encode("utf-8")) > 65536:
        raise ValueError("dynamic matrix output exceeds 64 KiB")
    try:
        resolved = json.loads(output.value, object_pairs_hook=unique_json_object)
        _literal(resolved)
    except (ValueError, RecursionError) as exc:
        raise ValueError("dynamic matrix output is not bounded literal JSON") from exc
    return resolved


def job_matrices(job: dict[str, YamlValue], *, outputs: tuple[ReplayOutput, ...] = (),
                 scope: tuple[JobIdentity, ...] = ()) -> tuple[str, ...]:
    strategy = job.get("strategy")
    if strategy is None:
        return ("null",)
    if not isinstance(strategy, dict):
        raise ValueError("matrix strategy is not statically proven")
    if "matrix" not in strategy:
        return ("null",)
    original = strategy["matrix"]
    raw = _resolved(original, job, outputs, scope)
    dynamic = raw is not original
    if not isinstance(raw, dict):
        raise ValueError("matrix expansion is not statically proven")
    resolved = {name: _resolved(value, job, outputs, scope) for name, value in raw.items()}
    dynamic = dynamic or any(resolved[name] is not raw[name] for name in raw)
    raw = resolved
    _literal(raw)
    axes = tuple(key for key in raw if key not in ("include", "exclude"))
    excludes = _entries(raw, "exclude")
    if any(set(item) - set(axes) for item in excludes):
        raise ValueError("matrix exclusion names an unknown axis")
    rows = [row for row in _product(raw, axes) if not any(_matches(row, item, axes) for item in excludes)]
    _apply_includes(rows, _entries(raw, "include"), axes)
    if len(rows) > MAX_MATRIX:
        raise ValueError("matrix expansion exceeds the bounded graph limit")
    if dynamic and not rows:
        raise ValueError("dynamic matrix cannot remove all execution coverage")
    encoded = tuple(matrix_json(row) for row in rows) or ("null",)
    if len(set(encoded)) != len(encoded):
        raise ValueError("matrix has duplicate execution identities")
    return encoded
