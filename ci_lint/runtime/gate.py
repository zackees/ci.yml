"""`ci-lint gate`: the "CI OK" aggregator (round-2A brief, part 2d; round-3A
brief Part 3 adds reuse verification).

Reads `plan.json` (`ci_lint plan`'s own `--out`/`--plan-out` JSON, which
carries `required_jobs` -- see `ci_lint.plan._required_job_ids` for the
job-id convention -- and, from round-3A, `lane_digests`) and `needs.json`
(GitHub Actions' `toJSON(needs)`, verbatim) and decides whether the run is
green:

  - every job in `plan["required_jobs"]` must be present in `needs` with
    `result == "success"`. A `skipped` required job is a failure (TEST-001
    spirit: a skip inside required coverage is not a pass) UNLESS it is a
    verified title-edit reuse -- see "Reuse verification" below.
  - a required job that is simply absent from `needs` (the workflow never
    defined a job with that id, or the gate job's own `needs:` list does
    not include it) is `needs_review`, never silently treated as a pass or
    a hard failure -- the naming convention may not match yet.
  - if `plan["mergeable"]` is false, the run is never green regardless of
    job results.

## Reuse verification (round-3A brief, Part 3)

A required job whose `needs` result is `skipped` is SUCCESS iff:

  1. the plan's own reuse map (`--reuse <reuse.json>`, `ci_lint.reuse`'s
     `ReuseResult.to_json_dict()`) marks the job's lane(s) as reused, AND
  2. when a GitHub token is available (`fetch`/`token`/`repo` all set),
     the referenced job is re-fetched live and confirmed `conclusion ==
     "success"`, its `name` contains the exact bracketed digest
     (`plan["lane_digests"][lane]`) for that lane, and (when `head_sha` is
     known) the referenced job's own `head_sha` matches.

`fast` and `dylint` map to the reuse map's own `"fast"`/`"dylint"` keys
1:1. `platform-build`/`platform-run` are matrix aggregates over every
selected platform lane (GitHub folds every matrix leg into one
`needs.<id>.result`), so they verify as reused only when **every**
currently-selected `"platform:<id>"` lane (read from `plan["lane_digests"]`)
is itself reused -- i.e. exactly when the plan's `platform_lanes_todo_json`
would be empty.

Without a token, a job the plan marks reused but that cannot be live-
verified is `needs_review` (exit non-zero) -- never a silent pass (round-3A
brief, Part 3: "without a token (local), a reused-but-unverified job is
needs_review"). A job the plan does *not* mark reused, or that fails live
verification (wrong digest, wrong head SHA, not `success`), is the
pre-existing TEST-001 failure -- a skip is not a pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ci_lint.cargo_messages import JsonValue
from ci_lint.finding import Finding, Status
from ci_lint.github_api import FetchFn, GitHubApiError

_TAG_REMOVES_RE = re.compile(r"tag '\[(?P<tag>[^\]]+)\]'.*removes")

# round-3A brief, Part 3: platform-build/platform-run are matrix
# aggregates, so their reuse coverage is every currently-selected
# "platform:<id>" lane, not a single reuse-map key of their own.
_MATRIX_JOB_IDS: frozenset[str] = frozenset({"platform-build", "platform-run"})


def _as_dict(value: JsonValue) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class ReuseVerdict:
    """The outcome of checking one required job's skip against the plan's
    reuse map (and, when possible, live GitHub state).

      - "ok": a verified reuse -- the job counts as success.
      - "needs_review": the plan marks it reused but it could not be (or
        was not) live-verified -- never silently a pass.
      - "fail": not marked reused, or verification failed outright -- the
        pre-existing "a skip is a failure" behavior.
    """

    status: str
    run_id: int | None


def _lane_keys_for_job(job_id: str, lane_digests: dict[str, JsonValue]) -> list[str]:
    if job_id in ("fast", "dylint"):
        return [job_id]
    if job_id in _MATRIX_JOB_IDS:
        return sorted(k for k in lane_digests if k.startswith("platform:"))
    return []


def _verify_reuse(  # noqa: C901
    job_id: str,
    plan: dict[str, JsonValue],
    reuse: dict[str, JsonValue] | None,
    head_sha: str | None,
    fetch: FetchFn | None,
    token: str | None,
    repo: str | None,
) -> ReuseVerdict:
    if reuse is None:
        return ReuseVerdict(status="fail", run_id=None)

    lane_digests_raw = plan.get("lane_digests")
    lane_digests = lane_digests_raw if isinstance(lane_digests_raw, dict) else {}
    lane_keys = _lane_keys_for_job(job_id, lane_digests)
    if not lane_keys:
        # A job with no lane mapping (unknown job id, or a matrix job
        # whose plan selected zero platform lanes -- which should never
        # itself be "required" in that case) is never treated as reused.
        return ReuseVerdict(status="fail", run_id=None)

    entries: list[tuple[str, dict[str, JsonValue], str]] = []
    for lane in lane_keys:
        raw_entry = reuse.get(lane)
        digest = lane_digests.get(lane)
        if not isinstance(raw_entry, dict) or not isinstance(digest, str):
            return ReuseVerdict(status="fail", run_id=None)
        entries.append((lane, raw_entry, digest))

    first_run_id_raw = entries[0][1].get("run_id")
    first_run_id = first_run_id_raw if isinstance(first_run_id_raw, int) else None

    if fetch is None or token is None or repo is None:
        return ReuseVerdict(status="needs_review", run_id=first_run_id)

    for _lane, entry, digest in entries:
        run_id = entry.get("run_id")
        job_id_ref = entry.get("job_id")
        if not isinstance(run_id, int) or not isinstance(job_id_ref, int):
            return ReuseVerdict(status="fail", run_id=first_run_id)
        try:
            job_payload = fetch(f"https://api.github.com/repos/{repo}/actions/jobs/{job_id_ref}", token)
        except GitHubApiError:
            # The API being unreachable to VERIFY a claimed reuse is not
            # the same as it being down for the plan step -- treat it as
            # unverifiable, not as proof the reuse is bogus.
            return ReuseVerdict(status="needs_review", run_id=first_run_id)
        job = job_payload if isinstance(job_payload, dict) else {}
        name = job.get("name")
        conclusion = job.get("conclusion")
        job_head_sha = job.get("head_sha")
        if conclusion != "success":
            return ReuseVerdict(status="fail", run_id=first_run_id)
        if not isinstance(name, str) or f"[{digest}]" not in name:
            return ReuseVerdict(status="fail", run_id=first_run_id)
        if head_sha is not None and isinstance(job_head_sha, str) and job_head_sha != head_sha:
            return ReuseVerdict(status="fail", run_id=first_run_id)

    return ReuseVerdict(status="ok", run_id=first_run_id)


@dataclass(frozen=True)
class JobStatus:
    job_id: str
    result: str | None  # None: absent from needs.json entirely
    ok: bool
    reused_from_run: int | None = None


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


def compute_gate(
    plan: dict[str, JsonValue],
    needs: dict[str, JsonValue],
    *,
    reuse: dict[str, JsonValue] | None = None,
    head_sha: str | None = None,
    fetch: FetchFn | None = None,
    token: str | None = None,
    repo: str | None = None,
) -> GateReport:
    """`reuse`/`head_sha`/`fetch`/`token`/`repo` are all optional and all
    default to "no reuse information available" -- a call with none of
    them behaves exactly as before round-3A (every pre-existing call site
    and test is unaffected). See the module docstring's "Reuse
    verification" section."""

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

        if result_str == "skipped":
            verdict = _verify_reuse(job_id, plan, reuse, head_sha, fetch, token, repo)
            if verdict.status == "ok":
                statuses.append(
                    JobStatus(job_id=job_id, result=result_str, ok=True, reused_from_run=verdict.run_id)
                )
                continue
            if verdict.status == "needs_review":
                findings.append(
                    Finding(
                        rule="TEST-001",
                        status=Status.NEEDS_REVIEW,
                        path=job_id,
                        message=f"required job '{job_id}' is skipped and the plan marks it reused from "
                        f"run {verdict.run_id}, but it could not be verified live (no GITHUB_TOKEN, "
                        "or the GitHub API was unreachable) -- treated as needs_review, never a "
                        "silent pass",
                        fix="run 'ci-lint gate' where GITHUB_TOKEN is set (in CI) so the reused job "
                        "can be verified live, or re-run the job so it is not skipped",
                    )
                )
                statuses.append(
                    JobStatus(job_id=job_id, result=result_str, ok=False, reused_from_run=verdict.run_id)
                )
                continue
            findings.append(
                Finding(
                    rule="TEST-001",
                    path=job_id,
                    message=f"required job '{job_id}' result is 'skipped' and is not a verified reuse "
                    "(a skipped required job counts as a failure unless the plan's reuse map proves "
                    "it, with a matching digest and head SHA, live)",
                    fix=f"fix job '{job_id}' so it completes with result 'success'; if it was meant to "
                    "be reused, check that the plan's --reuse map, lane_digests, and head SHA all "
                    "match the referenced run",
                )
            )
            statuses.append(JobStatus(job_id=job_id, result=result_str, ok=False))
            continue

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
        "statuses": [
            {"job_id": s.job_id, "result": s.result, "ok": s.ok, "reused_from_run": s.reused_from_run}
            for s in report.statuses
        ],
        "findings": [
            {"rule": f.rule, "status": f.status.value, "path": f.path, "line": f.line, "message": f.message, "fix": f.fix}
            for f in report.findings
        ],
        "mergeable": report.mergeable,
        "not_mergeable_message": report.not_mergeable_message,
        "ok": report.ok,
    }


def render_text(report: GateReport) -> str:
    lines = ["ci-lint gate:", f"{'job':<30} {'result':<12} {'ok':<5} note"]
    for s in report.statuses:
        note = f"reused from run {s.reused_from_run}" if s.reused_from_run is not None else ""
        lines.append(f"{s.job_id:<30} {s.result or '(absent)':<12} {'yes' if s.ok else 'no':<5} {note}")
    lines.append("")
    for f in report.findings:
        lines.append(f.render())
    if report.not_mergeable_message:
        lines.append(report.not_mergeable_message)
    lines.append(f"ci-lint gate: {'OK' if report.ok else 'NOT OK'}")
    return "\n".join(lines)
