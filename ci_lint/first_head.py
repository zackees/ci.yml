"""Workflow-scoped earliest-head CI evidence, separate from GATE-004 (#270)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Sequence

from ci_lint.cargo_messages import JsonValue

if TYPE_CHECKING:
    from ci_lint.first_pass import RunSample

Outcome = Literal["pass", "fail", "unknown"]
FAILED_CONCLUSIONS = frozenset(("failure", "timed_out", "startup_failure"))


@dataclass(frozen=True)
class FirstHeadOutcome:
    head_sha: str
    outcome: Outcome
    reason: str
    attested: bool = False
    created_at: str = ""
    run_ids: tuple[int, ...] = ()
    cohort_size: int = 0

    def to_json_dict(self) -> dict[str, JsonValue]:
        return {"head_sha": self.head_sha, "outcome": self.outcome,
                "reason": self.reason, "attested": self.attested,
                "created_at": self.created_at, "run_ids": list(self.run_ids), "cohort_size": self.cohort_size}


@dataclass(frozen=True)
class PrFirstHead:
    number: int
    title: str
    evidence: FirstHeadOutcome

    def to_json_dict(self) -> dict[str, JsonValue]:
        return {"number": self.number, "title": self.title, "evidence": self.evidence.to_json_dict()}


@dataclass(frozen=True)
class FirstHeadSummary:
    passed: int
    failed: int
    unknown: int
    attested_passed: int
    attested_failed: int
    attested_unknown: int

    @property
    def known_rate(self) -> float | None:
        known = self.passed + self.failed
        return self.passed / known if known else None

    @property
    def coverage(self) -> float | None:
        total = self.passed + self.failed + self.unknown
        return (self.passed + self.failed) / total if total else None

    def to_json_dict(self) -> dict[str, JsonValue]:
        return {"passed": self.passed, "failed": self.failed, "unknown": self.unknown,
                "known_rate": self.known_rate, "coverage": self.coverage,
                "attested_passed": self.attested_passed, "attested_failed": self.attested_failed,
                "attested_unknown": self.attested_unknown}


def summarize(samples: Sequence[PrFirstHead]) -> FirstHeadSummary:
    evidence = tuple(sample.evidence for sample in samples)
    return FirstHeadSummary(
        sum(e.outcome == "pass" for e in evidence),
        sum(e.outcome == "fail" for e in evidence),
        sum(e.outcome == "unknown" for e in evidence),
        sum(e.attested and e.outcome == "pass" for e in evidence),
        sum(e.attested and e.outcome == "fail" for e in evidence),
        sum(e.attested and e.outcome == "unknown" for e in evidence),
    )


@dataclass(frozen=True)
class TimedRun:
    run: RunSample
    at: datetime


@dataclass(frozen=True)
class FirstHeadSelection:
    head_sha: str
    runs: tuple[RunSample, ...]
    reason: str = ""
    created_at: str = ""


def _select(runs: Sequence[RunSample]) -> FirstHeadSelection:
    if not runs:
        return FirstHeadSelection("", (), "no-runs")
    timed: list[TimedRun] = []
    for run in runs:
        if not run.head_sha:
            return FirstHeadSelection("", (), "missing-head-sha")
        if not run.created_at:
            return FirstHeadSelection("", (), "missing-created-at")
        try:
            at = datetime.fromisoformat(run.created_at.replace("Z", "+00:00"))
        except ValueError:
            return FirstHeadSelection("", (), "invalid-created-at")
        if at.tzinfo is None:
            return FirstHeadSelection("", (), "invalid-created-at")
        timed.append(TimedRun(run, at))
    first = min(sample.at for sample in timed)
    heads = {sample.run.head_sha for sample in timed if sample.at == first}
    if len(heads) != 1:
        return FirstHeadSelection("", (), "ambiguous-first-head")
    sha = next(iter(heads))
    return FirstHeadSelection(sha, tuple(sample.run for sample in timed if sample.at == first), created_at=first.isoformat())


def _unknown_reason(runs: Sequence[RunSample]) -> str:
    if any(not run.attempt_known or run.attempt < 1 for run in runs):
        return "missing-attempt"
    if any(run.attempt > 1 for run in runs):
        return "rerun-history"
    if any(run.status != "completed" for run in runs):
        return "incomplete-run"
    if any(run.conclusion != "success" for run in runs):
        return "unresolved-conclusion"
    return ""


def _outcome(selected: FirstHeadSelection, outcome: Outcome, reason: str) -> FirstHeadOutcome:
    return FirstHeadOutcome(
        selected.head_sha, outcome, reason,
        attested=any(run.attested for run in selected.runs),
        created_at=selected.created_at,
        run_ids=tuple(run.run_id for run in selected.runs if run.run_id is not None),
        cohort_size=len(selected.runs),
    )


def classify_first_head(runs: Sequence[RunSample]) -> FirstHeadOutcome:
    """Never infer attempt-one success from an overwritten rerun or a later head.

    A recorded completed attempt-one failure proves failure despite other
    incomplete evidence. Otherwise the earliest observed workflow-run cohort
    must be completed attempt-one successes. Later duplicate triggers do not
    change that initial outcome. Same-head timestamp ties stay together;
    cancellation and neutral/skipped conclusions remain unknown. No extra
    GitHub calls recover missing attempts.
    """
    selected = _select(runs)
    if selected.reason:
        return FirstHeadOutcome("", "unknown", selected.reason)
    if any(run.attempt_known and run.attempt == 1 and run.status == "completed"
           and run.conclusion in FAILED_CONCLUSIONS for run in selected.runs):
        return _outcome(selected, "fail", "failed-conclusion")
    reason = _unknown_reason(selected.runs)
    if reason:
        return _outcome(selected, "unknown", reason)
    return _outcome(selected, "pass", "completed-first-attempt")
