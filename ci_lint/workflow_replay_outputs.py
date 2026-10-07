"""Bounded outputs from jobs already accepted by the existing execution proof."""

import json
import re
from dataclasses import dataclass
from collections.abc import Iterable, Sequence

from ci_lint.cargo_messages import JsonValue
from ci_lint.workflow_replay_identity import JobIdentity

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,255}")


@dataclass(frozen=True)
class ReplayOutput:
    identity: tuple[JobIdentity, ...]
    name: str
    value: str
    seq: int


def _outputs(raw: JsonValue, identity: tuple[JobIdentity, ...], seq: int) -> tuple[ReplayOutput, ...]:
    if not isinstance(raw, dict) or not 1 <= len(raw) <= 256:
        raise ValueError("workflow replay outputs must be a nonempty bounded string map")
    values: list[ReplayOutput] = []
    for name, value in raw.items():
        if _NAME.fullmatch(name) is None or not isinstance(value, str):
            raise ValueError("workflow replay output name or value is invalid")
        values.append(ReplayOutput(identity, name, value, seq))
    if len(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 65536:
        raise ValueError("workflow replay output payload exceeds 64 KiB")
    return tuple(values)


def proved_outputs(job: dict[str, JsonValue], identity: tuple[JobIdentity, ...], *,
                   excluded: bool) -> tuple[ReplayOutput, ...]:
    """Call only after the same job passes source-derived execution validation.

    These values are not graph coverage or permission to skip. Dynamic expansion
    must still resolve their source declarations and prove the complete graph.
    """
    raw = job.get("output_evidence")
    if raw is None:
        return ()
    if not identity or excluded:
        raise ValueError("workflow replay outputs require qualified executed job proof")
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "seq", "values", "error"}:
        raise ValueError("workflow replay output evidence is malformed")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1 or raw["error"] is not None:
        raise ValueError("workflow replay output evidence is refused or incompatible")
    seq = raw["seq"]
    sections = job.get("sections")
    if not isinstance(sections, list):
        raise ValueError("workflow replay outputs have no executed producer checks")
    last = max((section["last_seq"] for section in sections if isinstance(section, dict)
                and type(section.get("last_seq")) is int), default=0)
    if type(seq) is not int or not 0 < last < seq:
        raise ValueError("workflow replay output evidence does not follow executed checks")
    return _outputs(raw["values"], identity, seq)


def unique_json_object(pairs: Iterable[Sequence[JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for pair in pairs:
        key = pair[0]
        if not isinstance(key, str) or key in result:
            raise ValueError("replay report has duplicate or invalid JSON keys")
        result[key] = pair[1]
    return result

