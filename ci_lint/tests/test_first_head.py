"""Actual earliest-head outcomes stay distinct from GATE-004 adoption (#270)."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import unittest

from ci_lint.cargo_messages import JsonValue
from ci_lint.first_head import classify_first_head
from ci_lint.first_pass import RunSample, classify, collect, render_text


EARLY = "2026-10-01T00:01:00Z"
LATER = "2026-10-01T00:02:00Z"


def run(sha: str = "first", conclusion: str = "success", attempt: int = 1,
        at: str = EARLY) -> RunSample:
    return RunSample(sha, conclusion, attempt, at, True)


@dataclass(frozen=True)
class OutcomeCase:
    name: str
    runs: tuple[RunSample, ...]
    outcome: str
    reason: str


class FirstHeadTest(unittest.TestCase):
    def test_green_revision_is_not_failed_first_head(self) -> None:
        runs = [run(), run("revision", at=LATER)]
        self.assertFalse(classify(runs).first_pass)
        evidence = classify_first_head(runs)
        self.assertEqual(evidence.head_sha, "first")
        self.assertEqual(evidence.outcome, "pass")
        self.assertTrue(evidence.attested)

    def test_failed_first_head_stays_failed_after_fix(self) -> None:
        evidence = classify_first_head([run(conclusion="failure"), run("fix", at=LATER)])
        self.assertEqual(evidence.outcome, "fail")
        self.assertEqual(evidence.head_sha, "first")

    def test_cohort_receipt_keeps_ids_and_normalized_time(self) -> None:
        evidence = classify_first_head([replace(run(), run_id=42), replace(run(at=LATER), run_id=43)])
        self.assertEqual(evidence.run_ids, (42,))
        self.assertEqual(evidence.created_at, "2026-10-01T00:01:00+00:00")
        self.assertEqual(evidence.cohort_size, 1)

    def test_fail_closed_cases(self) -> None:
        cases = (
            OutcomeCase("no runs", (), "unknown", "no-runs"),
            OutcomeCase("overwritten rerun", (run(attempt=2),), "unknown", "rerun-history"),
            OutcomeCase("cancelled", (run(conclusion="cancelled"),), "unknown", "unresolved-conclusion"),
            OutcomeCase("incomplete", (replace(run(), status="in_progress"),), "unknown", "incomplete-run"),
            OutcomeCase("missing attempt", (replace(run(), attempt_known=False),), "unknown", "missing-attempt"),
            OutcomeCase("ambiguous heads", (run(), run("other")), "unknown", "ambiguous-first-head"),
            OutcomeCase("missing SHA", (run(""),), "unknown", "missing-head-sha"),
            OutcomeCase("missing time", (run(at=""),), "unknown", "missing-created-at"),
            OutcomeCase("invalid time", (run(at="garbage"),), "unknown", "invalid-created-at"),
            OutcomeCase("timeout", (run(conclusion="timed_out"),), "fail", "failed-conclusion"),
            OutcomeCase("startup failure", (run(conclusion="startup_failure"),), "fail", "failed-conclusion"),
            OutcomeCase("green rerun cannot erase failure", (run(conclusion="failure"), run(attempt=2, at=LATER)), "fail", "failed-conclusion"),
            OutcomeCase("later duplicate cancelled run", (run(), run(conclusion="cancelled", at=LATER)), "pass", "completed-first-attempt"),
            OutcomeCase("initial cancelled run", (run(conclusion="cancelled"), run(at=LATER)), "unknown", "unresolved-conclusion"),
            OutcomeCase("same-time duplicate cancellation", (run(), run(conclusion="cancelled")), "unknown", "unresolved-conclusion"),
            OutcomeCase("unknown status", (replace(run(), status=""),), "unknown", "incomplete-run"),
            OutcomeCase("missing conclusion", (run(conclusion=""),), "unknown", "unresolved-conclusion"),
            OutcomeCase("timezone-less time", (run(at="2026-10-01T00:01:00"),), "unknown", "invalid-created-at"),
        )
        for case in cases:
            with self.subTest(case.name):
                evidence = classify_first_head(case.runs)
                self.assertEqual(evidence.outcome, case.outcome)
                self.assertEqual(evidence.reason, case.reason)

    def test_missing_runs_are_unknown_without_changing_strict_denominator(self) -> None:
        pulls: JsonValue = [
            {"number": n, "title": "t", "head": {"ref": f"branch{n}"},
             "created_at": "2026-10-01T00:00:00Z", "merged_at": "2026-10-01T01:00:00Z",
             "updated_at": "2026-10-01T01:00:00Z"} for n in (1, 2)
        ]

        def fetch(url: str, _token: str) -> JsonValue:
            if "/pulls?" in url:
                return pulls
            if "branch=branch2" in url:
                return {"workflow_runs": []}
            return {"workflow_runs": [
                {"path": ".github/workflows/ci.yml", "head_sha": "first", "conclusion": "success",
                 "run_attempt": 1, "status": "completed", "created_at": EARLY},
                {"path": ".github/workflows/ci.yml", "head_sha": "revision", "conclusion": "success",
                 "run_attempt": 1, "status": "completed", "created_at": LATER},
            ]}

        report = collect("owner/repo", "ci.yml", datetime(2026, 9, 30, tzinfo=timezone.utc), fetch, "token")
        self.assertEqual(report.rate, 0.0)
        self.assertEqual(len(report.prs), 1)
        self.assertEqual(report.without_runs, 1)
        self.assertEqual(report.first_head_summary.passed, 1)
        self.assertEqual(report.first_head_summary.failed, 0)
        self.assertEqual(report.first_head_summary.unknown, 1)
        self.assertIn("single-head", render_text(report))
        self.assertIn("unknown=1", render_text(report))
        self.assertIn("first_head", report.to_json_dict())

    def test_api_metadata_is_not_assumed_complete(self) -> None:
        pulls: JsonValue = [{"number": 1, "title": "t", "head": {"ref": "branch"},
                             "created_at": "2026-10-01T00:00:00Z", "merged_at": "2026-10-01T01:00:00Z",
                             "updated_at": "2026-10-01T01:00:00Z"}]
        metadata: tuple[dict[str, JsonValue], ...] = (
            {"created_at": EARLY},
            {"run_attempt": 1, "created_at": EARLY},
            {"run_attempt": 1, "status": "completed"},
            {"run_attempt": 1, "status": "completed", "created_at": "invalid"},
        )
        for fields in metadata:
            with self.subTest(fields=fields):
                def fetch(url: str, _token: str) -> JsonValue:
                    if "/pulls?" in url:
                        return pulls
                    sample: dict[str, JsonValue] = {"path": ".github/workflows/ci.yml",
                                                    "head_sha": "first", "conclusion": "success"}
                    sample.update(fields)
                    return {"workflow_runs": [sample]}

                report = collect("owner/repo", "ci.yml", datetime(2026, 9, 30, tzinfo=timezone.utc), fetch, "token")
                self.assertEqual(report.first_head_summary.unknown, 1)
                self.assertEqual(report.first_head_summary.passed, 0)


if __name__ == "__main__":
    unittest.main()
