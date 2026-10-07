"""Run a gate with a fresh replay report and reject unproved successful exits."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ci_lint.cargo_messages import JsonValue
from ci_lint.workflow_replay import ReplayExpectation, prove_replay
from ci_lint.workflow_replay_config import ReplayConfig
from ci_lint.workflow_replay_plan import build_plan
from ci_lint.full_run_receipt import FullRunEvidence, ReceiptPass

REPORT_ENV = "CI_LINT_GATE_REPLAY_REPORT"
MAX_REPORT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class CheckedCommand:
    returncode: int
    error: str | None = None
    full_run: FullRunEvidence | None = None


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


def _execute(repo: Path, argv: tuple[str, ...], report: Path, *, report_source: str,
             env: dict[str, str], stdout: BinaryIO | None) -> int:
    if report_source == "file":
        return subprocess.run(list(argv), cwd=repo, env=env, stdin=subprocess.DEVNULL,
                              stdout=stdout, stderr=subprocess.STDOUT if stdout is not None else None,
                              check=False).returncode
    if report_source != "stdout":
        raise ValueError("unknown replay report source")
    with report.open("wb") as receipt:
        process = subprocess.run(list(argv), cwd=repo, env=env, stdin=subprocess.DEVNULL,
                                 stdout=receipt, stderr=stdout, check=False)
    if stdout is not None:
        with report.open("rb") as receipt:
            shutil.copyfileobj(receipt, stdout)
    return process.returncode


def run_checked_command(repo: Path, argv: tuple[str, ...], config: ReplayConfig, *,
                        head: str, tree: str, lane: str | None = None,
                        env: dict[str, str] | None = None, stdout: BinaryIO | None = None) -> CheckedCommand:
    try:
        plan = build_plan(repo, config, lane=lane)
    except (OSError, ValueError) as exc:
        return CheckedCommand(1, f"workflow replay proof rejected before execution: {exc}")
    expectation = ReplayExpectation(config.repository, repo, head, tree, plan.workflow,
                                    plan.selected, config.mode, plan.required,
                                    "pr" if plan.event == "pull_request" else plan.event, plan.event,
                                    plan.inputs)
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="ci-replay-") as scratch:
        report = Path(scratch) / "report.json"
        child_env = dict(env) if env is not None else os.environ.copy()
        child_env[REPORT_ENV] = str(report)
        try:
            returncode = _execute(repo, argv, report, report_source=config.report_source,
                                  env=child_env, stdout=stdout)
            if returncode != 0:
                return CheckedCommand(returncode)
            prove_replay(_read_report(report), expectation)
        except (OSError, UnicodeError, ValueError) as exc:
            return CheckedCommand(1, f"workflow replay proof rejected: {exc}")
    evidence = (FullRunEvidence(tuple(ReceiptPass(lane, int(round(time.monotonic() - start))) for lane in plan.lanes))
                if plan.lanes else None)
    return CheckedCommand(0, full_run=evidence)
