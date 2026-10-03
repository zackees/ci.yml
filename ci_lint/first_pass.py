"""`ci-lint local-gate first-pass` (GATE-004, zackees/ci.yml#166): how many
merged pull requests passed remote CI on their first push.

Live and GET-only. For every PR merged in the window, the named workflow's
`pull_request` runs on the PR's head branch (bounded by the PR's own
creation/merge times) are grouped by head SHA:

`workflow` is the workflow *file* name (`ci.yml`), matched against each
run's `path`, because a run's `name` is its run-name and many repositories
set that to the PR title. Runs are read from the workflow-scoped endpoint
(`/actions/workflows/<file>/runs`, paginated), so `--workflow` really scopes
the sample; the report prints how many merged PRs were scanned and how many
had no run of that workflow at all (excluded from the rate), so two
workflows' reports are visibly different. Merged PRs are paginated with no
silent cap; `limit` (CLI `--limit`, default 0 = all) keeps only the newest N
merged PRs and the report says so.

- **first pass** = exactly one head SHA ever got a run, some run on it
  concluded `success` on attempt 1, and no run on it concluded `failure`.
  A duplicate-trigger run on the same SHA that was cancelled does not count
  against it; a second pushed SHA (a fix-up) or a rerun (attempt > 1) does.
- **attested** = the final head commit carries a `Local-Gate:` trailer
  (`ci_lint.local_gate`), read from the run's own `head_commit.message`.

GATE-004 is `needs_review` when at least `min_prs` PRs were sampled and the
first-pass rate is below `target` -- a trend signal for a human, never a
merge blocker. Internally every record is a frozen dataclass; the only
dictionaries are the GitHub JSON boundary, typed as `JsonValue`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from ci_lint.cargo_messages import JsonValue
from ci_lint.finding import Finding, Status
from ci_lint.github_api import FetchFn
from ci_lint.local_gate import parse_attestation

API = "https://api.github.com"
DEFAULT_TARGET = 0.8
DEFAULT_MIN_PRS = 5


@dataclass(frozen=True)
class RunSample:
    head_sha: str
    conclusion: str
    attempt: int
    created_at: str
    attested: bool


@dataclass(frozen=True)
class PrSample:
    number: int
    title: str
    head_shas: int
    failures: int
    reruns: int
    first_pass: bool
    attested: bool


@dataclass(frozen=True)
class FirstPassReport:
    repo: str
    workflow: str
    since: str
    prs: tuple[PrSample, ...]
    target: float
    min_prs: int
    merged_total: int = 0
    scanned: int = 0
    without_runs: int = 0
    limit: int = 0

    @property
    def rate(self) -> float | None:
        return sum(p.first_pass for p in self.prs) / len(self.prs) if self.prs else None

    def finding(self) -> Finding | None:
        rate = self.rate
        if rate is None or len(self.prs) < self.min_prs or rate >= self.target:
            return None
        return Finding(
            rule="GATE-004",
            path=f"github:{self.repo}",
            status=Status.NEEDS_REVIEW,
            message=f"first-push pass rate {rate:.0%} over {len(self.prs)} merged PRs since {self.since} "
            f"is below the {self.target:.0%} target",
            fix="inspect the failing first pushes below; move each failure class into the local gate "
            "(ci-lint local-gate run) or fix the flaky remote lane",
        )

    def to_json_dict(self) -> dict[str, JsonValue]:
        return {
            "repo": self.repo,
            "workflow": self.workflow,
            "since": self.since,
            "target": self.target,
            "rate": self.rate,
            "merged_total": self.merged_total,
            "scanned": self.scanned,
            "without_runs": self.without_runs,
            "limit": self.limit,
            "prs": [
                {
                    "number": p.number,
                    "title": p.title,
                    "head_shas": p.head_shas,
                    "failures": p.failures,
                    "reruns": p.reruns,
                    "first_pass": p.first_pass,
                    "attested": p.attested,
                }
                for p in self.prs
            ],
        }


def _d(value: JsonValue) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def _l(value: JsonValue) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def _s(value: JsonValue) -> str:
    return value if isinstance(value, str) else ""


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


@dataclass(frozen=True)
class PrClassification:
    head_shas: int
    failures: int
    reruns: int
    first_pass: bool
    attested: bool


def classify(runs: list[RunSample]) -> PrClassification:
    """Distinct head SHAs, failed runs, reruns, first-pass verdict, and
    whether the final head carried a Local-Gate trailer."""

    shas = {r.head_sha for r in runs}
    failures = sum(r.conclusion == "failure" for r in runs)
    reruns = sum(r.attempt > 1 for r in runs)
    first_pass = (
        len(shas) == 1
        and failures == 0
        and reruns == 0
        and any(r.conclusion == "success" and r.attempt == 1 for r in runs)
    )
    latest = max(runs, key=lambda r: r.created_at) if runs else None
    return PrClassification(len(shas), failures, reruns, first_pass, bool(latest and latest.attested))


def _merged_prs(repo: str, since: datetime, fetch: FetchFn, token: str) -> list[dict[str, JsonValue]]:
    out: list[dict[str, JsonValue]] = []
    page = 0
    while True:
        page += 1
        url = f"{API}/repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100&page={page}"
        batch = _l(fetch(url, token))
        for raw in batch:
            pr = _d(raw)
            merged = _s(pr.get("merged_at"))
            if merged and _ts(merged) >= since:
                out.append(pr)
        if len(batch) < 100 or (batch and _ts(_s(_d(batch[-1]).get("updated_at"))) < since):
            break
    return out


def _workflow_runs(repo: str, workflow: str, branch: str, fetch: FetchFn, token: str) -> list[JsonValue]:
    base = (f"{API}/repos/{repo}/actions/workflows/{quote(workflow, safe='')}/runs"
            f"?event=pull_request&branch={quote(branch, safe='')}&per_page=100")
    out: list[JsonValue] = []
    page = 0
    while True:
        page += 1
        batch = _l(_d(fetch(f"{base}&page={page}", token)).get("workflow_runs"))
        out.extend(batch)
        if len(batch) < 100:
            return out


def collect(repo: str, workflow: str, since: datetime, fetch: FetchFn, token: str,
            *, target: float = DEFAULT_TARGET, min_prs: int = DEFAULT_MIN_PRS, limit: int = 0) -> FirstPassReport:
    prs: list[PrSample] = []
    merged_prs = sorted(_merged_prs(repo, since, fetch, token), key=lambda p: _s(p.get("merged_at")), reverse=True)
    selected = merged_prs[:limit] if limit > 0 else merged_prs
    without_runs = 0
    for pr in selected:
        head = _d(pr.get("head"))
        branch = _s(head.get("ref"))
        if not branch:
            continue
        created, merged = _ts(_s(pr.get("created_at"))), _ts(_s(pr.get("merged_at")))
        runs: list[RunSample] = []
        for raw in _workflow_runs(repo, workflow, branch, fetch, token):
            run = _d(raw)
            # `name` is the run-name (often the PR title); `path` names the file.
            if _s(run.get("path")).split("@", 1)[0].rsplit("/", 1)[-1] != workflow:
                continue
            at = _s(run.get("created_at"))
            if not at or not (created <= _ts(at) <= merged):
                continue
            attempt = run.get("run_attempt")
            runs.append(
                RunSample(
                    head_sha=_s(run.get("head_sha")),
                    conclusion=_s(run.get("conclusion")),
                    attempt=attempt if isinstance(attempt, int) else 1,
                    created_at=at,
                    attested=parse_attestation(_s(_d(run.get("head_commit")).get("message"))) is not None,
                )
            )
        if not runs:
            without_runs += 1
            continue
        verdict = classify(runs)
        number = pr.get("number")
        prs.append(
            PrSample(
                number=number if isinstance(number, int) else 0,
                title=_s(pr.get("title")),
                head_shas=verdict.head_shas,
                failures=verdict.failures,
                reruns=verdict.reruns,
                first_pass=verdict.first_pass,
                attested=verdict.attested,
            )
        )
    prs.sort(key=lambda p: p.number)
    return FirstPassReport(repo=repo, workflow=workflow, since=since.isoformat(), prs=tuple(prs),
                           target=target, min_prs=min_prs, merged_total=len(merged_prs),
                           scanned=len(selected), without_runs=without_runs, limit=limit)


def render_text(report: FirstPassReport) -> str:
    capped = f" (--limit {report.limit}: newest {report.scanned} of {report.merged_total})" if report.limit else ""
    lines = [
        f"first-pass: {report.repo} workflow '{report.workflow}' since {report.since}",
        f"  merged PRs: {report.merged_total} total, {report.scanned} scanned{capped}; "
        f"{report.without_runs} had no pull_request run of '{report.workflow}' (excluded); "
        f"{len(report.prs)} sampled",
    ]
    for p in report.prs:
        mark = "PASS" if p.first_pass else "MISS"
        lines.append(
            f"  #{p.number:<6} {mark}  pushes={p.head_shas} failed_runs={p.failures} reruns={p.reruns} "
            f"attested={'yes' if p.attested else 'no'}  {p.title[:70]}"
        )
    rate = report.rate
    attested = sum(p.attested for p in report.prs)
    lines.append(
        f"  rate: {'n/a' if rate is None else f'{rate:.0%}'} first-pass over {len(report.prs)} PRs "
        f"(target {report.target:.0%}); attested heads: {attested}/{len(report.prs)}"
    )
    finding = report.finding()
    if finding is not None:
        lines.append(finding.render())
    return "\n".join(lines)
