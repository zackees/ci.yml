"""`ci-lint sync-issues --apply` (round M2-42, zackees/ci.yml#98).

A fake in-memory GitHub issue tracker (`FakeGitHub`) plays both `FetchStatusFn`
(GET the open, `ci-lint`-labelled issues) and `WriteFn` (POST create, PATCH
update/close, POST a close comment), so these tests never touch the network
and never run against a real repository. Each test is RED (the guard must
refuse) or GREEN (the guard allows exactly what it should).
"""

from __future__ import annotations

import unittest

from ci_lint.finding import Finding, Status
from ci_lint.fleet import FleetReport, RepoScan
from ci_lint.github_api import GitHubApiError
from ci_lint.sync_issues import API_ROOT, run_apply

REPO = "zackees/fake-repo"


def _scan(findings: tuple[Finding, ...], *, opt_in: bool) -> RepoScan:
    return RepoScan(
        repo=REPO,
        default_branch="main",
        ci_toml_schema=3,
        workflow_count=1,
        pr_entrypoints=(".github/workflows/ci.yml",),
        cache_bytes=None,
        findings=findings,
        sync_issues_opt_in=opt_in,
    )


class FakeGitHub:
    """An in-memory GitHub issue tracker limited to what `sync_issues`
    calls: list open `ci-lint` issues, create one, PATCH title/body or
    `state`, and POST a comment. Anything else raises, so an accidental
    extra call fails the test loudly instead of silently no-opping."""

    def __init__(self) -> None:
        self.issues: dict[int, dict[str, str]] = {}
        self._next = 1
        self.write_calls: list[tuple[str, str]] = []  # (method, url)

    def fetch_status(self, url: str, token: str) -> tuple[int, object]:
        if url == f"{API_ROOT}/repos/{REPO}/issues?state=open&labels=ci-lint&per_page=100":
            return 200, [
                {"number": n, "title": i["title"], "body": i["body"]}
                for n, i in self.issues.items()
                if i["state"] == "open"
            ]
        raise GitHubApiError(f"FakeGitHub: unexpected GET {url}")

    def write(self, url: str, token: str, method: str, payload: dict[str, object]) -> tuple[int, object]:
        self.write_calls.append((method, url))
        if method == "POST" and url == f"{API_ROOT}/repos/{REPO}/issues":
            number = self._next
            self._next += 1
            self.issues[number] = {"title": str(payload["title"]), "body": str(payload["body"]), "state": "open"}
            return 201, {"number": number}
        if method == "PATCH" and url.startswith(f"{API_ROOT}/repos/{REPO}/issues/") and not url.endswith("/comments"):
            number = int(url.rsplit("/", 1)[-1])
            issue = self.issues[number]
            if "title" in payload:
                issue["title"] = str(payload["title"])
                issue["body"] = str(payload["body"])
            if payload.get("state") == "closed":
                issue["state"] = "closed"
            return 200, {}
        if method == "POST" and url.endswith("/comments"):
            return 201, {}
        raise GitHubApiError(f"FakeGitHub: unexpected {method} {url}")


def _report(*scans: RepoScan) -> FleetReport:
    return FleetReport(owners=("zackees",), repos=tuple(scans), errors=())


F1 = Finding(rule="RUN-001", path="a.yml", message="x", fix="y")
F2 = Finding(rule="RUN-001", path="b.yml", message="x", fix="y")


class SyncIssuesApplyTest(unittest.TestCase):
    def test_red_no_opt_in_means_no_writes(self) -> None:
        gh = FakeGitHub()
        report = _report(_scan((F1,), opt_in=False))
        result = run_apply(report, gh.fetch_status, gh.write, "t", max_writes=5)
        self.assertEqual(result.executed, ())
        self.assertEqual(result.skipped_not_opted_in, (REPO,))
        self.assertEqual(gh.write_calls, [])
        self.assertEqual(gh.issues, {})

    def test_green_opted_in_creates(self) -> None:
        gh = FakeGitHub()
        report = _report(_scan((F1,), opt_in=True))
        result = run_apply(report, gh.fetch_status, gh.write, "t", max_writes=5)
        self.assertEqual([a.action for a in result.executed], ["create"])
        self.assertEqual(result.errors, ())
        self.assertEqual(len(gh.issues), 1)

    def test_red_cap_is_respected(self) -> None:
        gh = FakeGitHub()
        report = _report(_scan((F1, F2), opt_in=True))
        result = run_apply(report, gh.fetch_status, gh.write, "t", max_writes=1)
        self.assertEqual(len(result.executed), 1)
        self.assertEqual(len(result.skipped_cap), 1)
        self.assertEqual(len(gh.issues), 1)
        # A second run with the same cap must not re-plan the already-created
        # issue as another create -- it picks up the still-uncreated one.
        result2 = run_apply(report, gh.fetch_status, gh.write, "t", max_writes=1)
        self.assertEqual([a.action for a in result2.executed], ["create"])
        self.assertEqual(len(gh.issues), 2)

    def test_green_rerun_makes_no_duplicate_writes(self) -> None:
        gh = FakeGitHub()
        report = _report(_scan((F1, F2), opt_in=True))
        first = run_apply(report, gh.fetch_status, gh.write, "t", max_writes=5)
        self.assertEqual(len(first.executed), 2)
        self.assertEqual(len(gh.write_calls), 2)
        second = run_apply(report, gh.fetch_status, gh.write, "t", max_writes=5)
        self.assertEqual(second.executed, ())
        self.assertEqual(len(gh.write_calls), 2)  # unchanged: no new writes at all

    def test_green_resolved_finding_closes_its_issue(self) -> None:
        gh = FakeGitHub()
        opened = run_apply(_report(_scan((F1, F2), opt_in=True)), gh.fetch_status, gh.write, "t", max_writes=5)
        self.assertEqual(len(opened.executed), 2)
        self.assertTrue(all(i["state"] == "open" for i in gh.issues.values()))

        # F2's finding is gone now: its issue must close, F1's must be untouched.
        closed = run_apply(_report(_scan((F1,), opt_in=True)), gh.fetch_status, gh.write, "t", max_writes=5)
        self.assertEqual([a.action for a in closed.executed], ["close"])
        states = {n: i["state"] for n, i in gh.issues.items()}
        self.assertEqual(sorted(states.values()), ["closed", "open"])

    def test_red_never_writes_to_an_issue_without_the_fingerprint_marker(self) -> None:
        gh = FakeGitHub()
        # A pre-existing issue with the ci-lint label but NO fingerprint marker
        # (e.g. a human filed it by hand) must never be updated or closed.
        gh.issues[42] = {"title": "unrelated ci-lint issue", "body": "no marker here", "state": "open"}
        gh._next = 43
        report = _report(_scan((F1,), opt_in=True))
        result = run_apply(report, gh.fetch_status, gh.write, "t", max_writes=5)
        # fetch_existing itself drops bodies with no fingerprint match, so the
        # only action possible is a fresh create; issue 42 is left exactly as is.
        self.assertEqual([a.action for a in result.executed], ["create"])
        self.assertEqual(gh.issues[42], {"title": "unrelated ci-lint issue", "body": "no marker here", "state": "open"})


if __name__ == "__main__":
    unittest.main()
