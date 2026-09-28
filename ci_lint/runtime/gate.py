"""`ci-lint gate`: the "CI OK" aggregator (round-2A brief, part 2d).

Reads `plan.json` (`ci_lint plan`'s own `--out`/`--plan-out` JSON, which
carries `required_jobs` -- see `ci_lint.plan._required_job_ids` for the
job-id convention) and `needs.json` (GitHub Actions' `toJSON(needs)`,
verbatim) and decides whether the run is green:

  - every job in `plan["required_jobs"]` must be present in `needs` with
    `result == "success"`. A `skipped` required job is a failure (TEST-001
    spirit: a skip inside required coverage is not a pass).
  - a required job that is simply absent from `needs` (the workflow never
    defined a job with that id, or the gate job's own `needs:` list does
    not include it) is `needs_review`, never silently treated as a pass or
    a hard failure -- the naming convention may not match yet.
  - if `plan["mergeable"]` is false, the run is never green regardless of
    job results.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ci_lint.cargo_messages import JsonValue
from ci_lint.finding import Finding, Status

_TAG_REMOVES_RE = re.compile(r"tag '\[(?P<tag>[^\]]+)\]'.*removes")


def _as_dict(value: JsonValue) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class JobStatus:
    job_id: str
    result: str | None  # None: absent from needs.json entirely
    ok: bool


@dataclass(frozen=True)
class GateReport:
    required_jobs: tuple[str, ...]
    statuses: tuple[JobStatus, ...]
    findings: tuple[Finding, ...]
    mergeable: bool
    not_mergeable_message: str | None
    ok: bool


def _removing_tags(plan: dict[str, JsonValue]) -> list[str]:
    reasons = plan.get("reasons")
    tags: list[str] = []
    if isinstance(reasons, list):
        for r in reasons:
            if not isinstance(r, str):
                continue
            m = _TAG_REMOVES_RE.search(r)
            if m and m.group("tag") not in tags:
                tags.append(m.group("tag"))
    return sorted(tags)


def compute_gate(plan: dict[str, JsonValue], needs: dict[str, JsonValue]) -> GateReport:
    required_raw = plan.get("required_jobs")
    required = tuple(j for j in required_raw if isinstance(j, str)) if isinstance(required_raw, list) else ()
    mergeable = plan.get("mergeable") is not False

    findings: list[Finding] = []
    statuses: list[JobStatus] = []
    for job_id in required:
        entry = _as_dict(needs.get(job_id))
        if not entry:
            findings.append(
                Finding(
                    rule="TEST-001",
                    status=Status.NEEDS_REVIEW,
                    path=job_id,
                    message=f"job '{job_id}' is required by the plan but absent from needs.json",
                    fix=f"ensure ci.yml defines a job with id '{job_id}' and that the gate job's "
                    f"'needs:' list includes '{job_id}'",
                )
            )
            statuses.append(JobStatus(job_id=job_id, result=None, ok=False))
            continue
        result = entry.get("result")
        result_str = result if isinstance(result, str) else "unknown"
        ok = result_str == "success"
        if not ok:
            findings.append(
                Finding(
                    rule="TEST-001",
                    path=job_id,
                    message=f"required job '{job_id}' result is '{result_str}' "
                    "(a skipped required job counts as a failure)",
                    fix=f"fix job '{job_id}' so it completes with result 'success'; if it is genuinely "
                    "optional, it should not be in the plan's required_jobs",
                )
            )
        statuses.append(JobStatus(job_id=job_id, result=result_str, ok=ok))

    not_mergeable_message: str | None = None
    if not mergeable:
        tags = _removing_tags(plan)
        tag_list = ", ".join(tags) if tags else "unknown"
        not_mergeable_message = f"not mergeable: tag(s) {tag_list} remove required coverage"

    ok = mergeable and all(s.ok for s in statuses)
    return GateReport(
        required_jobs=required,
        statuses=tuple(statuses),
        findings=tuple(findings),
        mergeable=mergeable,
        not_mergeable_message=not_mergeable_message,
        ok=ok,
    )


def to_json_dict(report: GateReport) -> dict[str, object]:
    return {
        "required_jobs": list(report.required_jobs),
        "statuses": [{"job_id": s.job_id, "result": s.result, "ok": s.ok} for s in report.statuses],
        "findings": [
            {"rule": f.rule, "status": f.status.value, "path": f.path, "line": f.line, "message": f.message, "fix": f.fix}
            for f in report.findings
        ],
        "mergeable": report.mergeable,
        "not_mergeable_message": report.not_mergeable_message,
        "ok": report.ok,
    }


def render_text(report: GateReport) -> str:
    lines = ["ci-lint gate:", f"{'job':<30} {'result':<12} ok"]
    for s in report.statuses:
        lines.append(f"{s.job_id:<30} {s.result or '(absent)':<12} {'yes' if s.ok else 'no'}")
    lines.append("")
    for f in report.findings:
        lines.append(f.render())
    if report.not_mergeable_message:
        lines.append(report.not_mergeable_message)
    lines.append(f"ci-lint gate: {'OK' if report.ok else 'NOT OK'}")
    return "\n".join(lines)
