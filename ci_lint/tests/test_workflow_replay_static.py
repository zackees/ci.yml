"""Replay declarations cannot omit or ambiguously identify executable steps."""

import unittest

from ci_lint.finding import Status
from ci_lint.workflow_replay import ReplayJob
from ci_lint.workflow_replay_config import DeclaredReplayJob
from ci_lint.workflow_replay_static import _check_identity, _check_job


class ReplayStaticTest(unittest.TestCase):
    def test_exact_coverage(self) -> None:
        declaration = DeclaredReplayJob("ci.yml:test", ReplayJob("CI/Test", ("Checkout", "Tests")), ())
        self.assertEqual(_check_job(declaration, {"steps": [
            {"name": "Checkout", "uses": "actions/checkout@v4"},
            {"name": "Tests", "run": "pytest"},
        ]}, "ci.yml"), [])

    def test_omitted_setup_is_not_waived(self) -> None:
        declaration = DeclaredReplayJob("ci.yml:test", ReplayJob("CI/Test", ("Tests",)), ())
        findings = _check_job(declaration, {"steps": [
            {"name": "Checkout", "uses": "actions/checkout@v4"},
            {"name": "Tests", "run": "pytest"},
        ]}, "ci.yml")
        self.assertTrue(any("omitted=['Checkout']" in item.message for item in findings))

    def test_dynamic_and_duplicate_steps(self) -> None:
        declaration = DeclaredReplayJob("ci.yml:test", ReplayJob("CI/Test", ("Tests",)), ())
        findings = _check_job(declaration, {"steps": [
            {"name": "Tests", "run": "pytest"},
            {"name": "Tests", "run": "other-check"},
            {"run": "unnamed-check"},
        ]}, "ci.yml")
        self.assertTrue(any(item.status == Status.NEEDS_REVIEW for item in findings))
        self.assertTrue(any("duplicate" in item.message for item in findings))

    def test_matrix_and_reusable_are_unresolved(self) -> None:
        declaration = DeclaredReplayJob("ci.yml:test", ReplayJob("CI/Test", ("Tests",)), ())
        for job in ({"strategy": {}}, {"uses": "./.github/workflows/test.yml"}):
            with self.subTest(job=job):
                findings = _check_job(declaration, job, "ci.yml")
                self.assertEqual(findings[0].status, Status.NEEDS_REVIEW)

    def test_execution_identity(self) -> None:
        declaration = DeclaredReplayJob("ci.yml:test", ReplayJob("CI/Tests", ("Tests",)), ())
        self.assertEqual(_check_identity(declaration, {"name": "CI"}, {"name": "Tests"}, "ci.yml"), [])
        findings = _check_identity(declaration, {"name": "Other"}, {"name": "Tests"}, "ci.yml")
        self.assertEqual(findings[0].status, Status.VIOLATION)
        findings = _check_identity(declaration, {"name": "CI"}, {"name": "${{ matrix.name }}"}, "ci.yml")
        self.assertEqual(findings[0].status, Status.NEEDS_REVIEW)
        fallback = DeclaredReplayJob("ci.yml:test", ReplayJob("ci.yml/test", ("Tests",)), ())
        self.assertEqual(_check_identity(fallback, {}, {}, "ci.yml"), [])

    def test_literal_placeholder_is_not_a_check(self) -> None:
        declaration = DeclaredReplayJob("ci.yml:test", ReplayJob("CI/Test", ("Tests",)), ())
        findings = _check_job(declaration, {"steps": [
            {"name": "Placeholder", "if": "needs.select.outputs.test != 'true'",
             "run": 'echo "Linux integration runs in test and full CI"'},
            {"name": "Tests", "run": "pytest"},
        ]}, "ci.yml")
        self.assertEqual(findings, [])

    def test_echo_cannot_hide_commands_or_custom_shell(self) -> None:
        declaration = DeclaredReplayJob("ci.yml:test", ReplayJob("CI/Test", ("Tests",)), ())
        for command in ('echo "$(pytest)"', 'echo "ok"; pytest', 'echo "ok" > result',
                        'echo "ok"\npytest', 'echo "$RESULT"', 'echo "ok', 'echo "ok\''):
            with self.subTest(command=command):
                findings = _check_job(declaration, {"steps": [
                    {"name": "Other", "run": command}, {"name": "Tests", "run": "pytest"},
                ]}, "ci.yml")
                self.assertTrue(findings)
        findings = _check_job(declaration, {"steps": [
            {"name": "Other", "shell": "custom {0}", "run": 'echo "ok"'},
            {"name": "Tests", "run": "pytest"},
        ]}, "ci.yml")
        self.assertTrue(findings)
