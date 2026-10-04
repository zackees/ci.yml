"""Run a gate with a fresh replay report and reject unproved successful exits."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ci_lint.cargo_messages import JsonValue
from ci_lint.workflow_replay import ReplayExpectation, prove_replay
from ci_lint.workflow_replay_config import ReplayConfig

REPORT_ENV = "CI_LINT_GATE_REPLAY_REPORT"
MAX_REPORT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class CheckedCommand:
    returncode: int
    error: str | None = None


def _unique_object(pairs: Iterable[Sequence[JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for pair in pairs:
        key = pair[0]
        if not isinstance(key, str) or key in result:
            raise ValueError("replay report has duplicate or invalid JSON keys")
        result[key] = pair[1]
    return result


def _read_report(path: Path) -> JsonValue:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_REPORT_BYTES:
        raise ValueError("replay report must be a regular file no larger than 16 MiB")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


def run_checked_command(repo: Path, argv: tuple[str, ...], config: ReplayConfig, *,
                        head: str, tree: str, lane: str | None = None,
                        env: dict[str, str] | None = None, stdout: BinaryIO | None = None) -> CheckedCommand:
    jobs = tuple(job for job in config.jobs if lane is None or lane in job.lanes)
    if not jobs:
        return CheckedCommand(1, f"no workflow replay proof declared for lane {lane}")
    refs = {job.source_job for job in jobs}
    selected = next(iter(refs)).split(":", 1)[1] if lane is not None and len(refs) == 1 else None
    selection = next((item for item in config.selections if item.lane == lane), None)
    if lane is None and config.selections:
        return CheckedCommand(1, "multi-selection replay requires separate lane commands and reports")
    event = selection.event if selection is not None else "pull_request"
    expectation = ReplayExpectation(config.repository, repo, head, tree, config.workflow,
                                    selection.selected_job if selection is not None else selected,
                                    config.mode, tuple(job.proof for job in jobs),
                                    "pr" if event == "pull_request" else event, event,
                                    selection.inputs if selection is not None else ())
    with tempfile.TemporaryDirectory(prefix="ci-replay-") as scratch:
        report = Path(scratch) / "report.json"
        child_env = dict(env) if env is not None else os.environ.copy()
        child_env[REPORT_ENV] = str(report)
        try:
            process = subprocess.run(list(argv), cwd=repo, env=child_env, stdin=subprocess.DEVNULL,
                                     stdout=stdout, stderr=subprocess.STDOUT if stdout is not None else None,
                                     check=False)
            if process.returncode != 0:
                return CheckedCommand(process.returncode)
            prove_replay(_read_report(report), expectation)
        except (OSError, UnicodeError, ValueError) as exc:
            return CheckedCommand(1, f"workflow replay proof rejected: {exc}")
    return CheckedCommand(0)
