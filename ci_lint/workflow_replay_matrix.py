"""Bounded literal workflow matrices; expressions cannot prove coverage."""

import itertools
import json
import math

from ci_lint.workflow_replay_identity import matrix_json
from ci_lint.yaml_io import YamlValue

MAX_MATRIX = 256


def _literal(value: YamlValue) -> None:
    if isinstance(value, str) and "${{" in value:
        raise ValueError("dynamic matrix expansion is not statically proven")
    if isinstance(value, dict):
        for child in value.values():
            _literal(child)
    if isinstance(value, list):
        for child in value:
            _literal(child)


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


def job_matrices(job: dict[str, YamlValue]) -> tuple[str, ...]:
    strategy = job.get("strategy")
    if strategy is None:
        return ("null",)
    if not isinstance(strategy, dict):
        raise ValueError("matrix strategy is not statically proven")
    if "matrix" not in strategy:
        return ("null",)
    raw = strategy["matrix"]
    if not isinstance(raw, dict):
        raise ValueError("matrix expansion is not statically proven")
    _literal(raw)
    axes = tuple(key for key in raw if key not in ("include", "exclude"))
    excludes = _entries(raw, "exclude")
    if any(set(item) - set(axes) for item in excludes):
        raise ValueError("matrix exclusion names an unknown axis")
    rows = [row for row in _product(raw, axes) if not any(_matches(row, item, axes) for item in excludes)]
    _apply_includes(rows, _entries(raw, "include"), axes)
    if len(rows) > MAX_MATRIX:
        raise ValueError("matrix expansion exceeds the bounded graph limit")
    encoded = tuple(matrix_json(row) for row in rows) or ("null",)
    if len(set(encoded)) != len(encoded):
        raise ValueError("matrix has duplicate execution identities")
    return encoded
