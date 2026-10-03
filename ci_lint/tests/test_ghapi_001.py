"""GHAPI-001 (static): unbounded GitHub re-read loops -- ci_lint/rules/ghapi_reread.py (zackees/ci.yml#224)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from ci_lint.finding import Finding, Status
from ci_lint.rules.ghapi_reread import check_ghapi_001, scanned_scripts, shell_loops
from ci_lint.tests.helpers import requires_yaml_tooling


PY_POLL = """\
import subprocess
import time


def fetch(run_id):
    return subprocess.run(["gh", "run", "view", str(run_id), "--json", "status"], check=True)


def main(run_id):
    while True:
        state = fetch(run_id)
        if state:
            return
        time.sleep(20)
"""

PY_WRAPPER_POLL = """\
import subprocess
import time


def gh(*args):
    return subprocess.run(["gh", *args], check=False)


def gh_json(*args):
    return gh(*args)


def poll(repo, pr):
    while True:
        gh_json("api", f"repos/{repo}/issues/{pr}/comments?per_page=100")
        time.sleep(20)
"""


class _Repo(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel: str, text: str) -> None:
        (self.tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.tmp / rel).write_text(text, encoding="utf-8")

    def statuses(self, findings: list[Finding]) -> list[Status]:
        return [f.status for f in findings]


class ShellLoopsTest(unittest.TestCase):
    def test_nested_and_one_line_loops(self) -> None:
        lines = [
            "while true; do",
            "  for x in a b; do echo $x; done",
            "  sleep 5",
            "  echo done",
            "done",
            "until gh pr view 1; do sleep 2; done",
        ]
        spans = [(lp.kind, lp.start, lp.end) for lp in shell_loops(lines)]
        self.assertEqual([("while", 0, 4), ("for", 1, 1), ("until", 5, 5)], spans)


class PythonTest(_Repo):
    def test_while_true_sleep_direct_helper_read_is_violation(self) -> None:
        self.write("ci/watch.py", PY_POLL)
        findings = check_ghapi_001(self.tmp)
        self.assertEqual([Status.VIOLATION], self.statuses(findings))
        self.assertEqual(("ci/watch.py", 10), (findings[0].path, findings[0].line))
        self.assertIn("gh run view", findings[0].message)

    def test_read_through_gh_wrapper_is_violation(self) -> None:
        self.write("tools/github/pr_watch.py", PY_WRAPPER_POLL)
        findings = check_ghapi_001(self.tmp)
        self.assertEqual([Status.VIOLATION], self.statuses(findings))
        self.assertIn("gh api", findings[0].message)

    def test_since_bound_passes(self) -> None:
        self.write(
            "tools/github/pr_watch.py",
            PY_WRAPPER_POLL.replace("per_page=100", "per_page=100&since={hwm}"),
        )
        self.assertEqual([], check_ghapi_001(self.tmp))

    def test_conditional_header_passes(self) -> None:
        src = PY_POLL.replace(
            '"--json", "status"]', '"--json", "status", "-H", "If-None-Match: x"]'
        )
        self.write("ci/watch.py", src)
        self.assertEqual([], check_ghapi_001(self.tmp))

    def test_rest_urlopen_poll_is_violation(self) -> None:
        self.write(
            "ci/rest.py",
            "import time, urllib.request\nAPI = 'https://api.github.com'\n"
            "def main(u):\n    while True:\n        urllib.request.urlopen(f'{API}/repos/{u}/actions/runs')\n"
            "        time.sleep(30)\n",
        )
        self.assertEqual([Status.VIOLATION], self.statuses(check_ghapi_001(self.tmp)))

    def test_counted_retry_loop_is_needs_review(self) -> None:
        self.write(
            "ci/retry.py",
            "import subprocess, time\ndef main():\n    for _ in range(30):\n        time.sleep(2)\n"
            "        subprocess.run(['gh', 'run', 'list', '--limit', '10'])\n",
        )
        self.assertEqual(
            [Status.NEEDS_REVIEW], self.statuses(check_ghapi_001(self.tmp))
        )

    def test_non_literal_wrapper_call_is_needs_review(self) -> None:
        src = PY_WRAPPER_POLL.replace(
            'gh_json("api", f"repos/{repo}/issues/{pr}/comments?per_page=100")',
            "gh_json(cmd)",
        )
        self.write("ci/poll.py", src)
        self.assertEqual(
            [Status.NEEDS_REVIEW], self.statuses(check_ghapi_001(self.tmp))
        )

    def test_no_sleep_pagination_and_writes_are_clean(self) -> None:
        self.write(
            "ci/page.py",
            "import subprocess, time\ndef main():\n    while True:\n"
            "        subprocess.run(['gh', 'api', 'repos/o/r/issues', '-f', 'title=x'])\n        time.sleep(1)\n"
            "def pages():\n    while True:\n        subprocess.run(['gh', 'api', 'repos/o/r/pulls'])\n        break\n",
        )
        self.assertEqual([], check_ghapi_001(self.tmp))

    def test_allow_comment_on_loop_line(self) -> None:
        self.write(
            "ci/watch.py",
            PY_POLL.replace(
                "while True:", "while True:  # ci-lint: allow GHAPI-001 one-shot"
            ),
        )
        self.assertEqual([], check_ghapi_001(self.tmp))

    def test_gh_run_watch_is_violation(self) -> None:
        self.write(
            "ci/w.py",
            "import subprocess\nsubprocess.run(['gh', 'run', 'watch', '123'])\n",
        )
        findings = check_ghapi_001(self.tmp)
        self.assertEqual([Status.VIOLATION], self.statuses(findings))
        self.assertEqual(2, findings[0].line)

    def test_unscanned_paths_are_ignored(self) -> None:
        self.write("src/app/watch.py", PY_POLL)
        self.write(".claude/skills/land/watch.py", PY_POLL)
        self.assertEqual([".claude/skills/land/watch.py"], scanned_scripts(self.tmp))
        self.assertEqual(1, len(check_ghapi_001(self.tmp)))


class ShellScriptTest(_Repo):
    def test_while_sleep_gh_pr_checks_is_violation(self) -> None:
        self.write(
            "ci/wait.sh",
            "#!/bin/sh\nwhile true; do\n  gh pr checks 12 --json state\n  sleep 20\ndone\n",
        )
        findings = check_ghapi_001(self.tmp)
        self.assertEqual([Status.VIOLATION], self.statuses(findings))
        self.assertEqual(2, findings[0].line)

    def test_curl_with_etag_passes(self) -> None:
        self.write(
            "ci/wait.sh",
            'until false; do\n  curl -H "If-None-Match: $ETAG" https://api.github.com/repos/o/r/actions/runs\n'
            "  sleep 20\ndone\n",
        )
        self.assertEqual([], check_ghapi_001(self.tmp))

    def test_for_sleep_is_needs_review(self) -> None:
        self.write(
            "ci/wait.sh",
            "for i in $(seq 30); do gh run view 1 --json status; sleep 10; done\n",
        )
        self.assertEqual(
            [Status.NEEDS_REVIEW], self.statuses(check_ghapi_001(self.tmp))
        )

    def test_allow_comment_on_read_line(self) -> None:
        self.write(
            "ci/wait.sh",
            "while true; do\n  gh run view 1  # ci-lint: allow GHAPI-001 bounded by the caller's timeout\n"
            "  sleep 20\ndone\n",
        )
        self.assertEqual([], check_ghapi_001(self.tmp))

    def test_app_check_wait_left_to_gate_012(self) -> None:
        self.write(
            "ci/wait.sh",
            "while true; do\n  gh pr checks 1 | grep CodeRabbit\n  sleep 20\ndone\n",
        )
        self.assertEqual([], check_ghapi_001(self.tmp))


@requires_yaml_tooling
class WorkflowTest(_Repo):
    def test_run_block_poll_and_watch(self) -> None:
        self.write(
            ".github/workflows/release.yml",
            "on: push\njobs:\n  wait:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: |\n          while true; do\n            gh run list --limit 5\n            sleep 30\n"
            '          done\n      - run: gh run watch "$RUN_ID"\n',
        )
        findings = check_ghapi_001(self.tmp)
        self.assertEqual([Status.VIOLATION, Status.VIOLATION], self.statuses(findings))
        self.assertTrue(
            all(f.path == ".github/workflows/release.yml" for f in findings)
        )

    def test_run_watch_allow_comment(self) -> None:
        self.write(
            ".github/workflows/release.yml",
            "on: push\njobs:\n  wait:\n    runs-on: ubuntu-latest\n    steps:\n"
            '      - run: gh run watch "$RUN_ID"  # ci-lint: allow GHAPI-001 hosted token, not machine budget\n',
        )
        self.assertEqual([], check_ghapi_001(self.tmp))


if __name__ == "__main__":
    unittest.main()
