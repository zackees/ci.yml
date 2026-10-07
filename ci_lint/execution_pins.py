"""Typed execution-provider identity shared by status, receipts and lane keys."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields

from ci_lint.cargo_messages import JsonValue


@dataclass(frozen=True)
class ExecutionPins:
    interface_schema: int
    act_version: str
    act_binary_digest: str
    engine_manifest_digest: str
    engine_config_digest: str
    runner_manifest_digest: str
    runner_config_digest: str


def parse_execution_pins(raw: JsonValue) -> ExecutionPins:
    if not isinstance(raw, dict) or set(raw) != {field.name for field in fields(ExecutionPins)}:
        raise ValueError("execution provider pins are missing or have unknown fields")
    if type(raw["interface_schema"]) is not int or raw["interface_schema"] != 1:
        raise ValueError("execution provider interface schema is unsupported")
    version = raw["act_version"]
    match = re.fullmatch(r"\d+\.\d+\.\d+-act2\.(\d+)", version) if isinstance(version, str) else None
    if match is None or int(match.group(1)) < 11:
        raise ValueError("execution provider requires qualified act2.11 or later")
    digests: dict[str, str] = {}
    for field in fields(ExecutionPins)[2:]:
        value = raw[field.name]
        if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
            raise ValueError(f"execution provider digest is malformed: {field.name}")
        digests[field.name] = value
    return ExecutionPins(1, version, **digests)
