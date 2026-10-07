"""Immutable caller/matrix identities shared by replay expansion and proof."""

import json
import re
from dataclasses import dataclass

from ci_lint.cargo_messages import JsonValue


def matrix_json(value: JsonValue) -> str:
    if value is None or value == {}:
        return "null"
    if not isinstance(value, dict):
        raise ValueError("qualified identity matrix must be an object or null")
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("qualified identity matrix is not finite JSON") from exc
    if len(encoded.encode()) > 65536:
        raise ValueError("qualified identity matrix exceeds the contract limit")
    return encoded


@dataclass(frozen=True)
class JobIdentity:
    job_id: str
    matrix: str = "null"


def identity_part(job_id: str, matrix: JsonValue = None) -> JobIdentity:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", job_id) is None:
        raise ValueError("qualified identity has an invalid job ID")
    return JobIdentity(job_id, matrix_json(matrix))


def bounded_identity(parts: tuple[JobIdentity, ...]) -> tuple[JobIdentity, ...]:
    document = [{"jobID": part.job_id, "matrix": json.loads(part.matrix)} for part in parts]
    if not 1 <= len(parts) <= 32 or len(json.dumps(document, separators=(",", ":")).encode()) > 65536:
        raise ValueError("qualified identity exceeds the contract limit")
    return parts


def parse_identity(raw: JsonValue) -> tuple[JobIdentity, ...]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= 32:
        raise ValueError("qualified execution identity is missing or malformed")
    result: list[JobIdentity] = []
    for part in raw:
        if not isinstance(part, dict) or set(part) != {"jobID", "matrix"} or not isinstance(part["jobID"], str):
            raise ValueError("qualified execution identity has malformed components")
        result.append(identity_part(part["jobID"], part["matrix"]))
    return bounded_identity(tuple(result))
