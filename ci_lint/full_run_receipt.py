"""Proof required before one full local run seeds distinct GATE-007 lanes."""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from pathlib import Path

from ci_lint.cargo_messages import JsonValue
from ci_lint.finding import Finding
from ci_lint.toml_cursor import Cursor, TomlValue

RECEIPT_FORMAT = "lane-passes-v1"
MAX_RECEIPT_BYTES = 64 * 1024


@dataclass(frozen=True)
class FullRunConfig:
    min_misses: int


@dataclass(frozen=True)
class ReceiptPass:
    lane: str
    secs: int


@dataclass(frozen=True)
class FullRunEvidence:
    passes: tuple[ReceiptPass, ...]
    not_applicable: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReceiptLoad:
    evidence: FullRunEvidence | None
    error: str | None


def parse_full_run(raw: dict[str, TomlValue], *, path: str, source: str,
                   findings: list[Finding]) -> FullRunConfig | None:
    sub = Cursor(raw, path, findings, source)
    receipt = sub.str_("receipt")
    min_misses = sub.int_("min-misses", required=False)
    sub.finish()
    if receipt != RECEIPT_FORMAT:
        findings.append(Finding(rule="GATE-007", path=source, message=f"{path}.receipt must be {RECEIPT_FORMAT!r}",
                                fix=f"set receipt = \"{RECEIPT_FORMAT}\""))
        return None
    threshold = min_misses if min_misses is not None else 2
    if threshold < 2:
        findings.append(Finding(rule="GATE-007", path=source, message=f"{path}.min-misses must be at least 2",
                                fix="use 2 or more; a single miss already runs its named lane"))
        return None
    return FullRunConfig(min_misses=threshold)


def load_receipt(path: Path, *, tree: str, expected_lanes: tuple[str, ...],
                 optional_lanes: tuple[str, ...] = ()) -> ReceiptLoad:
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            return ReceiptLoad(None, "receipt must be a regular file")
        if metadata.st_size > MAX_RECEIPT_BYTES:
            return ReceiptLoad(None, "receipt exceeds 64 KiB")
        raw: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return ReceiptLoad(None, f"receipt missing or invalid: {exc}")
    if not isinstance(raw, dict) or raw.get("version") != 1 or raw.get("tree") != tree:
        return ReceiptLoad(None, "receipt version or tree differs from the gated commit")
    raw_passes = raw.get("passes")
    if not isinstance(raw_passes, list):
        return ReceiptLoad(None, "receipt passes must be a list")
    passes: list[ReceiptPass] = []
    for item in raw_passes:
        if not isinstance(item, dict):
            return ReceiptLoad(None, "receipt pass must be an object")
        lane, secs = item.get("lane"), item.get("secs")
        if not isinstance(lane, str) or not isinstance(secs, int) or isinstance(secs, bool) or secs < 0:
            return ReceiptLoad(None, "receipt pass needs a lane and nonnegative integer secs")
        passes.append(ReceiptPass(lane, secs))
    return _account_lanes(tuple(passes), raw.get("not-applicable", []), expected_lanes, optional_lanes)


def _account_lanes(passes: tuple[ReceiptPass, ...], absent: JsonValue,
                   expected_lanes: tuple[str, ...], optional_lanes: tuple[str, ...]) -> ReceiptLoad:
    if not isinstance(absent, list) or any(not isinstance(lane, str) for lane in absent):
        return ReceiptLoad(None, "receipt not-applicable must be a list of lane names")
    unavailable = tuple(lane for lane in absent if isinstance(lane, str))
    if not set(unavailable) <= set(optional_lanes):
        return ReceiptLoad(None, "only declared optional lanes may be not applicable")
    accounted = [p.lane for p in passes] + list(unavailable)
    if len(accounted) != len(expected_lanes) or set(accounted) != set(expected_lanes):
        return ReceiptLoad(None, "receipt must account for each declared lane exactly once")
    return ReceiptLoad(FullRunEvidence(passes, unavailable), None)
