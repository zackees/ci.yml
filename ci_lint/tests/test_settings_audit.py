"""`ci-lint audit` -- ci_lint/settings_audit.py (round-5 brief, deliverable
1: SEC-005/006/007, GEN-006/011). Every test injects a fake `FetchStatusFn`
mapping (url -> (status, body)) -- no network. Distinct from
ci_lint.cache.audit's own test_cache_audit.py.
"""

from __future__ import annotations

import unittest

from ci_lint.finding import Status
from ci_lint.github_api import GitHubApiError
from ci_lint.schema import load_ci_toml
from ci_lint.settings_audit import DEFAULT_GATE_CHECK_NAME, run_audit
from ci_lint.tests.helpers import FIXTURES

REPO = FIXTURES / "runtime" / "cache" / "repo"  # [publish.pypi].environment = "pypi"
REPO_SLUG = "zackees/template-python-rust-cmd"
TOKEN = "t"
API = "https://api.github.com"


def _fetch_status_from(table: dict[str, tuple[int, object]]):
    def fetch_status(url: str, token: str):
        assert token == TOKEN
        if url not in table:
            raise AssertionError(f"unexpected URL in test: {url}")
        return table[url]

    return fetch_status


def _clean_table(*, default_branch: str = "main", gate_check_name: str = DEFAULT_GATE_CHECK_NAME) -> dict[str, tuple[int, object]]:
    """Every endpoint returning a "nothing to report" answer -- individual
    tests override one entry at a time to exercise one rule."""

    return {
        f"{API}/repos/{REPO_SLUG}/actions/secrets": (200, {"total_count": 0, "secrets": []}),
        f"{API}/repos/{REPO_SLUG}/environments/pypi/secrets": (200, {"total_count": 0, "secrets": []}),
        f"{API}/repos/{REPO_SLUG}/environments/pypi": (
            200,
            {"deployment_branch_policy": {"protected_branches": True, "custom_branch_policies": False}},
        ),
        f"{API}/repos/{REPO_SLUG}/actions/permissions/workflow": (200, {"default_workflow_permissions": "read"}),
        f"{API}/repos/{REPO_SLUG}/branches/{default_branch}/protection": (
            200,
            {"required_status_checks": {"strict": False, "contexts": [gate_check_name]}},
        ),
        f"{API}/repos/{REPO_SLUG}/rulesets": (200, []),
    }


class SettingsAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        ci, findings = load_ci_toml(REPO)
        assert ci is not None, findings
        self.ci = ci

    def _run(self, table: dict[str, tuple[int, object]], **kwargs):
        return run_audit(self.ci, fetch_status=_fetch_status_from(table), token=TOKEN, repo=REPO_SLUG, **kwargs)

    # ── clean baseline ──────────────────────────────────────────────────

    def test_clean_repo_has_no_findings(self) -> None:
        report = self._run(_clean_table())
        self.assertEqual([], list(report.findings), [f.render() for f in report.findings])

    # ── SEC-005 ─────────────────────────────────────────────────────────

    def test_sec_005_repo_secret_exists_is_violation(self) -> None:
        table = _clean_table()
        table[f"{API}/repos/{REPO_SLUG}/actions/secrets"] = (
            200, {"total_count": 1, "secrets": [{"name": "PYPI_TOKEN"}]}
        )
        report = self._run(table)
        f = next(f for f in report.findings if f.rule == "SEC-005")
        self.assertEqual(Status.VIOLATION, f.status)
        self.assertIn("PYPI_TOKEN", f.message)

    def test_sec_005_environment_secret_exists_is_violation(self) -> None:
        table = _clean_table()
        table[f"{API}/repos/{REPO_SLUG}/environments/pypi/secrets"] = (200, {"total_count": 2, "secrets": []})
        report = self._run(table)
        f = next(f for f in report.findings if f.rule == "SEC-005")
        self.assertEqual(Status.VIOLATION, f.status)
        self.assertIn("environment:pypi", f.path)

    def test_sec_005_forbidden_is_needs_review_never_pass(self) -> None:
        table = _clean_table()
        table[f"{API}/repos/{REPO_SLUG}/actions/secrets"] = (403, {"message": "Forbidden"})
        report = self._run(table)
        f = next(f for f in report.findings if f.rule == "SEC-005")
        self.assertEqual(Status.NEEDS_REVIEW, f.status)
        self.assertIn("admin scope", f.fix)

    # ── SEC-006 ─────────────────────────────────────────────────────────

    def test_sec_006_environment_missing_is_violation(self) -> None:
        table = _clean_table()
        table[f"{API}/repos/{REPO_SLUG}/environments/pypi"] = (404, {"message": "Not Found"})
        report = self._run(table)
        f = next(f for f in report.findings if f.rule == "SEC-006")
        self.assertEqual(Status.VIOLATION, f.status)
        self.assertIn("does not exist", f.message)

    def test_sec_006_no_deployment_branch_policy_is_violation(self) -> None:
        table = _clean_table()
        table[f"{API}/repos/{REPO_SLUG}/environments/pypi"] = (200, {"deployment_branch_policy": None})
        report = self._run(table)
        f = next(f for f in report.findings if f.rule == "SEC-006")
        self.assertEqual(Status.VIOLATION, f.status)

    def test_sec_006_custom_policy_restricted_to_default_branch_is_clean(self) -> None:
        table = _clean_table()
        table[f"{API}/repos/{REPO_SLUG}/environments/pypi"] = (
            200, {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}
        )
        table[f"{API}/repos/{REPO_SLUG}/environments/pypi/deployment-branch-policies"] = (
            200, {"branch_policies": [{"name": "main"}]}
        )
        report = self._run(table)
        self.assertEqual([], [f for f in report.findings if f.rule == "SEC-006"])

    def test_sec_006_custom_policy_allows_extra_branch_is_violation(self) -> None:
        table = _clean_table()
        table[f"{API}/repos/{REPO_SLUG}/environments/pypi"] = (
            200, {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}
        )
        table[f"{API}/repos/{REPO_SLUG}/environments/pypi/deployment-branch-policies"] = (
            200, {"branch_policies": [{"name": "main"}, {"name": "release/*"}]}
        )
        report = self._run(table)
        f = next(f for f in report.findings if f.rule == "SEC-006")
        self.assertEqual(Status.VIOLATION, f.status)
        self.assertIn("not restricted", f.message)

    def test_sec_006_undeclared_publish_environment_is_needs_review(self) -> None:
        from dataclasses import replace

        ci_no_publish = replace(self.ci, publish=replace(self.ci.publish, pypi=None))
        report = run_audit(ci_no_publish, fetch_status=_fetch_status_from(_clean_table()), token=TOKEN, repo=REPO_SLUG)
        f = next(f for f in report.findings if f.rule == "SEC-006")
        self.assertEqual(Status.NEEDS_REVIEW, f.status)

    # ── SEC-007 ─────────────────────────────────────────────────────────

    def test_sec_007_write_permissions_is_violation(self) -> None:
        table = _clean_table()
        table[f"{API}/repos/{REPO_SLUG}/actions/permissions/workflow"] = (
            200, {"default_workflow_permissions": "write"}
        )
        report = self._run(table)
        f = next(f for f in report.findings if f.rule == "SEC-007")
        self.assertEqual(Status.VIOLATION, f.status)
        self.assertIn("'write'", f.message)

    def test_sec_007_read_permissions_is_clean(self) -> None:
        report = self._run(_clean_table())
        self.assertEqual([], [f for f in report.findings if f.rule == "SEC-007"])

    def test_sec_007_forbidden_is_needs_review(self) -> None:
        table = _clean_table()
        table[f"{API}/repos/{REPO_SLUG}/actions/permissions/workflow"] = (403, {"message": "Forbidden"})
        report = self._run(table)
        f = next(f for f in report.findings if f.rule == "SEC-007")
        self.assertEqual(Status.NEEDS_REVIEW, f.status)

    # ── GEN-006 ─────────────────────────────────────────────────────────

    def test_gen_006_strict_without_queue_is_violation(self) -> None:
        table = _clean_table()
        table[f"{API}/repos/{REPO_SLUG}/branches/main/protection"] = (
            200, {"required_status_checks": {"strict": True, "contexts": [DEFAULT_GATE_CHECK_NAME]}}
        )
        report = self._run(table)
        f = next(f for f in report.findings if f.rule == "GEN-006")
        self.assertEqual(Status.VIOLATION, f.status)
        self.assertIn("no active merge-queue", f.message)

    def test_gen_006_strict_with_active_merge_queue_is_clean(self) -> None:
        table = _clean_table()
        table[f"{API}/repos/{REPO_SLUG}/branches/main/protection"] = (
            200, {"required_status_checks": {"strict": True, "contexts": [DEFAULT_GATE_CHECK_NAME]}}
        )
        table[f"{API}/repos/{REPO_SLUG}/rulesets"] = (200, [{"id": 1}])
        table[f"{API}/repos/{REPO_SLUG}/rulesets/1"] = (
            200,
            {
                "enforcement": "active",
                "target": "branch",
                "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
                "rules": [{"type": "merge_queue"}],
            },
        )
        report = self._run(table)
        self.assertEqual([], [f for f in report.findings if f.rule == "GEN-006"])

    def test_gen_006_disabled_ruleset_does_not_count(self) -> None:
        table = _clean_table()
        table[f"{API}/repos/{REPO_SLUG}/branches/main/protection"] = (
            200, {"required_status_checks": {"strict": True, "contexts": [DEFAULT_GATE_CHECK_NAME]}}
        )
        table[f"{API}/repos/{REPO_SLUG}/rulesets"] = (200, [{"id": 1}])
        table[f"{API}/repos/{REPO_SLUG}/rulesets/1"] = (
            200,
            {
                "enforcement": "disabled",
                "target": "branch",
                "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
                "rules": [{"type": "merge_queue"}],
            },
        )
        report = self._run(table)
        self.assertTrue(any(f.rule == "GEN-006" for f in report.findings))

    def test_gen_006_no_branch_protection_is_none_not_a_finding(self) -> None:
        table = _clean_table()
        table[f"{API}/repos/{REPO_SLUG}/branches/main/protection"] = (404, {"message": "Not Found"})
        report = self._run(table)
        self.assertEqual([], [f for f in report.findings if f.rule == "GEN-006"])

    def test_gen_006_strict_false_is_clean_regardless_of_rulesets(self) -> None:
        report = self._run(_clean_table())  # strict: False in the clean table
        self.assertEqual([], [f for f in report.findings if f.rule == "GEN-006"])

    def test_gen_006_forbidden_branch_protection_is_needs_review(self) -> None:
        table = _clean_table()
        table[f"{API}/repos/{REPO_SLUG}/branches/main/protection"] = (403, {"message": "Forbidden"})
        report = self._run(table)
        f = next(f for f in report.findings if f.rule == "GEN-006")
        self.assertEqual(Status.NEEDS_REVIEW, f.status)

    # ── GEN-011 ─────────────────────────────────────────────────────────

    def test_gen_011_missing_gate_check_is_violation(self) -> None:
        table = _clean_table()
        table[f"{API}/repos/{REPO_SLUG}/branches/main/protection"] = (
            200, {"required_status_checks": {"strict": False, "contexts": ["lint", "test"]}}
        )
        report = self._run(table)
        f = next(f for f in report.findings if f.rule == "GEN-011")
        self.assertEqual(Status.VIOLATION, f.status)
        self.assertIn("CI OK", f.message)

    def test_gen_011_gate_check_present_via_checks_array_is_clean(self) -> None:
        table = _clean_table()
        table[f"{API}/repos/{REPO_SLUG}/branches/main/protection"] = (
            200,
            {"required_status_checks": {"strict": False, "checks": [{"context": DEFAULT_GATE_CHECK_NAME, "app_id": -1}]}},
        )
        report = self._run(table)
        self.assertEqual([], [f for f in report.findings if f.rule == "GEN-011"])

    def test_gen_011_no_branch_protection_is_needs_review(self) -> None:
        table = _clean_table()
        table[f"{API}/repos/{REPO_SLUG}/branches/main/protection"] = (404, {"message": "Not Found"})
        report = self._run(table)
        f = next(f for f in report.findings if f.rule == "GEN-011")
        self.assertEqual(Status.NEEDS_REVIEW, f.status)
        self.assertIn("policy decision", f.fix)

    def test_gen_011_custom_gate_check_name(self) -> None:
        table = _clean_table(gate_check_name="my-gate")
        report = self._run(table, gate_check_name="my-gate")
        self.assertEqual([], [f for f in report.findings if f.rule == "GEN-011"])
        report_default = self._run(table)  # default "CI OK" not present -> violation
        f = next(f for f in report_default.findings if f.rule == "GEN-011")
        self.assertEqual(Status.VIOLATION, f.status)

    # ── network failure never crashes ────────────────────────────────────

    def test_network_failure_is_needs_review_not_a_crash(self) -> None:
        def raising_fetch_status(url: str, token: str):
            raise GitHubApiError("boom")

        report = run_audit(self.ci, fetch_status=raising_fetch_status, token=TOKEN, repo=REPO_SLUG)
        self.assertTrue(report.findings)
        self.assertTrue(all(f.status == Status.NEEDS_REVIEW for f in report.findings))

    def test_exit_code_convention_violation_present(self) -> None:
        table = _clean_table()
        table[f"{API}/repos/{REPO_SLUG}/actions/permissions/workflow"] = (
            200, {"default_workflow_permissions": "write"}
        )
        report = self._run(table)
        self.assertTrue(any(f.status == Status.VIOLATION for f in report.findings))
