"""`ci-lint sync-issues --dry-run`: the fingerprinted issue plan for a fleet
scan (zackees/ci.yml#38; docs/agent-guide.md "Findings and issue
lifecycle").

One issue per (repository, rule ID, subject), where the subject is the
finding's file (or `repo:<owner/name>` for a settings finding). The
fingerprint is the first 16 hex digits of sha256("<repo>|<rule>|<subject>")
and is embedded in the issue body as `<!-- ci-lint-fingerprint: <fp> -->`,
so a later run finds the same issue again regardless of title edits.

Only `violation` findings are planned; a `needs_review` finding is never
filed (agent-guide.md: "Do not file issues from a single ambiguous run").
Issues are planned in the scanned repository itself, labelled `ci-lint`.

This round is dry-run ONLY: the module computes and prints the exact
create/update/close actions, reading existing issues with GET requests. It
has no write path at all -- enabling writes is a tracked follow-up.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from ci_lint.finding import Finding, Status
from ci_lint.fleet import API_ROOT, FleetReport, RepoScan
from ci_lint.github_api import FetchStatusFn, GitHubApiError

ISSUE_LABEL = "ci-lint"
_FP_RE = re.compile(r"<!--\s*ci-lint-fingerprint:\s*([0-9a-f]{16})\s*-->")


@dataclass(frozen=True)
class ExistingIssue:
    number: int
    title: str
    body: str
    fingerprint: str


@dataclass(frozen=True)
class IssueAction:
    action: str  # "create" | "update" | "unchanged" | "close"
    repo: str
    fingerprint: str
    rule: str
    subject: str
    title: str
    body: str
    number: int | None = None


def subject_of(finding: Finding, repo: str) -> str:
    return finding.path or f"repo:{repo}"


def fingerprint(repo: str, rule: str, subject: str) -> str:
    return hashlib.sha256(f"{repo}|{rule}|{subject}".encode("utf-8")).hexdigest()[:16]


def render_issue(repo: str, rule: str, subject: str, findings: list[Finding]) -> tuple[str, str]:
    fp = fingerprint(repo, rule, subject)
    title = f"ci-lint {rule}: {subject}"
    lines = [
        f"<!-- ci-lint-fingerprint: {fp} -->",
        f"`ci-lint fleet scan` reports **{rule}** for `{subject}` in `{repo}`.",
        "",
        "Findings:",
    ]
    for f in sorted(findings, key=lambda x: (x.line or 0, x.message)):
        loc = f.location()
        lines.append(f"- `{loc}`: {f.message}")
    lines.extend(["", f"Fix: {findings[0].fix}", "", "Policy: https://github.com/zackees/ci.yml (docs/policy-*.md)."])
    return title, "\n".join(lines)


def fetch_existing(fetch_status: FetchStatusFn, token: str, repo: str) -> tuple[list[ExistingIssue], str | None]:
    url = f"{API_ROOT}/repos/{repo}/issues?state=open&labels={ISSUE_LABEL}&per_page=100"
    try:
        status, body = fetch_status(url, token)
    except GitHubApiError as exc:
        return [], f"{repo}: listing issues failed ({exc})"
    if status == 404:
        return [], None
    if status != 200 or not isinstance(body, list):
        return [], f"{repo}: listing issues failed (HTTP {status})"
    issues: list[ExistingIssue] = []
    for item in body:
        if not isinstance(item, dict) or "pull_request" in item:
            continue
        text = item.get("body")
        text = text if isinstance(text, str) else ""
        match = _FP_RE.search(text)
        number = item.get("number")
        if match is None or not isinstance(number, int):
            continue
        title = item.get("title")
        issues.append(
            ExistingIssue(number=number, title=title if isinstance(title, str) else "", body=text, fingerprint=match.group(1))
        )
    return issues, None


def plan_repo(scan: RepoScan, existing: list[ExistingIssue]) -> list[IssueAction]:
    groups: dict[tuple[str, str], list[Finding]] = {}
    for f in scan.findings:
        if f.status != Status.VIOLATION:
            continue
        groups.setdefault((f.rule, subject_of(f, scan.repo)), []).append(f)
    by_fp = {e.fingerprint: e for e in existing}
    actions: list[IssueAction] = []
    seen: set[str] = set()
    for (rule, subject), findings in sorted(groups.items()):
        fp = fingerprint(scan.repo, rule, subject)
        seen.add(fp)
        title, body = render_issue(scan.repo, rule, subject, findings)
        prior = by_fp.get(fp)
        if prior is None:
            kind, number = "create", None
        elif prior.body.strip() == body.strip() and prior.title == title:
            kind, number = "unchanged", prior.number
        else:
            kind, number = "update", prior.number
        actions.append(IssueAction(kind, scan.repo, fp, rule, subject, title, body, number))
    for prior in existing:
        if prior.fingerprint not in seen:
            actions.append(
                IssueAction(
                    "close",
                    scan.repo,
                    prior.fingerprint,
                    "",
                    "",
                    prior.title,
                    "`ci-lint fleet scan` no longer reports this finding.",
                    prior.number,
                )
            )
    return actions


def plan_sync(
    report: FleetReport, fetch_status: FetchStatusFn | None, token: str
) -> tuple[list[IssueAction], list[str]]:
    """`fetch_status=None` plans offline (every issue is `create`)."""

    actions: list[IssueAction] = []
    errors: list[str] = []
    for scan in report.repos:
        existing: list[ExistingIssue] = []
        if fetch_status is not None:
            existing, err = fetch_existing(fetch_status, token, scan.repo)
            if err:
                errors.append(err)
        actions.extend(plan_repo(scan, existing))
    return actions, errors


def to_json_list(actions: list[IssueAction]) -> list[dict[str, str | int | None]]:
    return [
        {
            "action": a.action,
            "repo": a.repo,
            "number": a.number,
            "fingerprint": a.fingerprint,
            "rule": a.rule,
            "subject": a.subject,
            "labels": ISSUE_LABEL,
            "title": a.title,
            "body": a.body,
        }
        for a in actions
    ]


def render_text(actions: list[IssueAction], errors: list[str]) -> str:
    counts: dict[str, int] = {}
    for a in actions:
        counts[a.action] = counts.get(a.action, 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing to do"
    lines = [f"ci-lint sync-issues --dry-run: {summary} (no writes performed)"]
    for a in actions:
        target = f"{a.repo}#{a.number}" if a.number is not None else a.repo
        lines.extend(["", f"=== WOULD {a.action.upper()} {target} [{a.fingerprint}] label={ISSUE_LABEL}", f"title: {a.title}"])
        if a.action != "unchanged":
            lines.append(a.body)
    if errors:
        lines.extend(["", "errors:"])
        lines.extend(f"  {e}" for e in errors)
    return "\n".join(lines)
