"""GATE-012: checks act cannot run never gate a PR -- ci_lint/remote_only.py (zackees/ci.yml#206)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from ci_lint.finding import Finding, Status
from ci_lint.remote_only import (
    Confinement,
    check_coderabbit,
    check_gate_012,
    check_pr_workflows,
    check_required_checks,
    check_waits,
    classify_if,
)
from ci_lint.tests.helpers import requires_yaml_tooling

SUPPRESSED = (
    "reviews:\n  auto_review:\n    enabled: false\n  commit_status: false\n"
    "  fail_commit_status: false\n  request_changes_workflow: false\n"
)


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


class ClassifyIfTest(unittest.TestCase):
    def test_confined_forms(self) -> None:
        for expr in (
            "github.event_name != 'pull_request'",
            "${{ github.event_name == 'push' }}",
            "startsWith(github.ref, 'refs/tags/v')",
            "github.ref == 'refs/heads/main' && success()",
            "contains(github.event.pull_request.labels.*.name, 'ci-full')",
            "github.event_name == 'push' || github.event_name == 'schedule'",
            False,
        ):
            self.assertEqual(classify_if(expr), Confinement.CONFINED, expr)

    def test_unconfined_and_unknown(self) -> None:
        self.assertEqual(classify_if(None), Confinement.NONE)
        self.assertEqual(classify_if("success()"), Confinement.UNKNOWN)
        # An `||` with one PR-reaching disjunct is not confined.
        self.assertEqual(classify_if("github.event_name == 'push' || github.event_name == 'pull_request'"), Confinement.UNKNOWN)


@requires_yaml_tooling
class CodeRabbitTest(_Repo):
    def test_missing_is_violation(self) -> None:
        findings = check_coderabbit(self.tmp)
        self.assertEqual(self.statuses(findings), [Status.VIOLATION])
        self.assertIn("no .coderabbit.yaml", findings[0].message)

    def test_suppressed_passes(self) -> None:
        self.write(".coderabbit.yaml", SUPPRESSED)
        self.assertEqual(check_coderabbit(self.tmp), [])

    def test_yml_spelling_accepted(self) -> None:
        self.write(".coderabbit.yml", SUPPRESSED)
        self.assertEqual(check_coderabbit(self.tmp), [])

    def test_defaults_left_on(self) -> None:
        self.write(".coderabbit.yaml", "language: en-US\nreviews:\n  request_changes_workflow: true\n")
        messages = [f.message for f in check_coderabbit(self.tmp)]
        self.assertEqual(len(messages), 3, messages)
        self.assertTrue(any("auto_review.enabled" in m for m in messages))
        self.assertTrue(any("commit_status" in m for m in messages))
        self.assertTrue(any("request_changes_workflow" in m for m in messages))


WF_HEAD = "on:\n  pull_request:\n  push:\n    branches: [main]\npermissions:\n  contents: read\njobs:\n"


@requires_yaml_tooling
class PrWorkflowTest(_Repo):
    def test_unconditional_codeql_on_pr_is_violation(self) -> None:
        self.write(".github/workflows/ci.yml", WF_HEAD + "  scan:\n    runs-on: ubuntu-24.04\n    steps:\n"
                   "      - uses: github/codeql-action/init@v3\n      - uses: github/codeql-action/analyze@v3\n")
        findings = check_pr_workflows(self.tmp)
        self.assertEqual(self.statuses(findings), [Status.VIOLATION, Status.VIOLATION])
        self.assertIn("codeql-action/init", findings[0].message)

    def test_confined_job_and_step_pass(self) -> None:
        self.write(".github/workflows/ci.yml", WF_HEAD +
                   "  pages:\n    if: github.event_name != 'pull_request'\n    runs-on: ubuntu-24.04\n"
                   "    permissions:\n      id-token: write\n      pages: write\n"
                   "    steps:\n      - uses: actions/deploy-pages@v4\n"
                   "  cov:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: soldr cargo test\n"
                   "      - uses: codecov/codecov-action@v5\n        if: github.ref == 'refs/heads/main'\n")
        self.assertEqual(check_pr_workflows(self.tmp), [])

    def test_unknown_if_is_needs_review(self) -> None:
        self.write(".github/workflows/ci.yml", WF_HEAD + "  cov:\n    if: success()\n    runs-on: ubuntu-24.04\n"
                   "    steps:\n      - uses: codecov/codecov-action@v5\n")
        self.assertEqual(self.statuses(check_pr_workflows(self.tmp)), [Status.NEEDS_REVIEW])

    def test_oidc_on_pr(self) -> None:
        self.write(".github/workflows/ci.yml", WF_HEAD.replace("contents: read", "id-token: write") +
                   "  build:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo hi\n"
                   "  lint:\n    runs-on: ubuntu-24.04\n    permissions:\n      contents: read\n    steps:\n      - run: echo hi\n")
        findings = check_pr_workflows(self.tmp)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("job 'build'", findings[0].message)
        self.assertIn("id-token", findings[0].message)

    def test_non_pr_workflow_ignored(self) -> None:
        self.write(".github/workflows/release.yml", "on:\n  push:\n    tags: ['v*']\njobs:\n  publish:\n"
                   "    runs-on: ubuntu-24.04\n    permissions:\n      id-token: write\n"
                   "    steps:\n      - uses: pypa/gh-action-pypi-publish@release/v1\n")
        self.assertEqual(check_pr_workflows(self.tmp), [])

    def test_local_reusable_workflow_inherits_pr_context(self) -> None:
        self.write(".github/workflows/ci.yml", WF_HEAD + "  call:\n    uses: ./.github/workflows/_scan.yml\n"
                   "  gated:\n    if: github.event_name == 'push'\n    uses: ./.github/workflows/_pages.yml\n")
        self.write(".github/workflows/_scan.yml", "on:\n  workflow_call:\njobs:\n  scan:\n    runs-on: ubuntu-24.04\n"
                   "    steps:\n      - uses: actions/dependency-review-action@v4\n")
        self.write(".github/workflows/_pages.yml", "on:\n  workflow_call:\njobs:\n  deploy:\n    runs-on: ubuntu-24.04\n"
                   "    steps:\n      - uses: actions/deploy-pages@v4\n")
        findings = check_pr_workflows(self.tmp)
        self.assertEqual([f.path for f in findings], [".github/workflows/_scan.yml"])


@requires_yaml_tooling
class WaitTest(_Repo):
    def test_wait_action_naming_coderabbit(self) -> None:
        self.write(".github/workflows/land.yml", "on: [push]\njobs:\n  land:\n    runs-on: ubuntu-24.04\n    steps:\n"
                   "      - uses: lewagon/wait-on-check-action@v1\n        with:\n          check-name: CodeRabbit\n")
        findings = check_waits(self.tmp)
        self.assertEqual(self.statuses(findings), [Status.VIOLATION])

    def test_run_polling_for_coderabbit(self) -> None:
        self.write(".github/workflows/land.yml", "on: [push]\njobs:\n  land:\n    runs-on: ubuntu-24.04\n    steps:\n"
                   "      - run: gh pr checks --watch | grep -q CodeRabbit\n")
        self.assertEqual(self.statuses(check_waits(self.tmp)), [Status.VIOLATION])

    def test_ci_script_and_allow_marker(self) -> None:
        self.write("ci/land.py", "import time\n"
                   "def wait():\n    while status('CodeRabbit') is None:\n        time.sleep(5)\n"
                   "IGNORED = 'coderabbitai[bot]'  # ci-lint: allow GATE-012 filters the bot's comments out\n")
        findings = check_waits(self.tmp)
        self.assertEqual([(f.status, f.line) for f in findings], [(Status.VIOLATION, 3)])

    def test_mention_without_polling_is_needs_review(self) -> None:
        self.write("ci/notes.sh", "echo 'CodeRabbit is advisory only'\n")
        self.assertEqual(self.statuses(check_waits(self.tmp)), [Status.NEEDS_REVIEW])

    def test_clean_repo(self) -> None:
        self.write(".coderabbit.yaml", SUPPRESSED)
        self.write(".github/workflows/ci.yml", WF_HEAD + "  lint:\n    runs-on: ubuntu-24.04\n    steps:\n"
                   "      - uses: actions/checkout@v4\n      - run: python3 ci/gate.py\n")
        self.assertEqual(check_gate_012(self.tmp), [])


class RequiredChecksTest(unittest.TestCase):
    def test_app_named_and_foreign_app_contexts(self) -> None:
        protection = {
            "required_status_checks": {
                "contexts": ["CI OK", "CodeRabbit"],
                "checks": [
                    {"context": "CI OK", "app_id": 15368},
                    {"context": "CodeRabbit", "app_id": None},
                    {"context": "Vercel", "app_id": 8329},
                ],
            }
        }
        findings = check_required_checks(protection, None, "main")
        self.assertEqual(len(findings), 2, findings)
        self.assertIn("CodeRabbit", findings[0].message)
        self.assertIn("8329", findings[1].message)

    def test_rulesets(self) -> None:
        rulesets = [
            {"name": "main", "enforcement": "active", "rules": [
                {"type": "required_status_checks", "parameters": {"required_status_checks": [
                    {"context": "CI OK", "integration_id": 15368},
                    {"context": "codecov/patch"},
                ]}},
            ]},
            {"name": "off", "enforcement": "disabled", "rules": [
                {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "CodeRabbit"}]}},
            ]},
        ]
        findings = check_required_checks(None, rulesets, "main")
        self.assertEqual([f.message.split("'")[1] for f in findings], ["codecov/patch"])

    def test_unreadable_is_silent(self) -> None:
        self.assertEqual(check_required_checks(None, None, "main"), [])


if __name__ == "__main__":
    unittest.main()
