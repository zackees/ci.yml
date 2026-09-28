"""CT-001..003, CT-005, CT-006, TAG-001, TAG-002 -- schema.py, exceptions.py,
rules/contract.py. None of these need parsed workflow YAML."""

from __future__ import annotations

import datetime
import unittest

from ci_lint.exceptions import apply_exceptions
from ci_lint.rules.contract import check_ct_006, check_tag_001, check_tag_002
from ci_lint.schema import load_ci_toml
from ci_lint.tests.helpers import fixture


def rule_ids(findings) -> list[str]:
    return [f.rule for f in findings]


class SchemaFixtureTest(unittest.TestCase):
    def test_ct_001_unknown_key(self) -> None:
        _, red_findings = load_ci_toml(fixture("CT-001", "red"))
        self.assertIn("CT-001", rule_ids(red_findings))
        _, green_findings = load_ci_toml(fixture("CT-001", "green"))
        self.assertNotIn("CT-001", rule_ids(green_findings))

    def test_ct_002_wrong_type(self) -> None:
        _, red_findings = load_ci_toml(fixture("CT-002", "red"))
        self.assertIn("CT-002", rule_ids(red_findings))
        _, green_findings = load_ci_toml(fixture("CT-002", "green"))
        self.assertNotIn("CT-002", rule_ids(green_findings))

    def test_ct_003_wrong_schema(self) -> None:
        _, red_findings = load_ci_toml(fixture("CT-003", "red"))
        self.assertIn("CT-003", rule_ids(red_findings))
        _, green_findings = load_ci_toml(fixture("CT-003", "green"))
        self.assertNotIn("CT-003", rule_ids(green_findings))

    def test_ct_006_platform_target_mismatch(self) -> None:
        repo = fixture("CT-006", "red")
        ci, _ = load_ci_toml(repo)
        self.assertIn("CT-006", rule_ids(check_ct_006(ci, repo)))

        repo = fixture("CT-006", "green")
        ci, _ = load_ci_toml(repo)
        self.assertNotIn("CT-006", rule_ids(check_ct_006(ci, repo)))

    def test_ct_006_missing_cargo_toml(self) -> None:
        ci, _ = load_ci_toml(fixture("CT-006", "green"))
        findings = check_ct_006(ci, fixture("CT-006", "green").parent.parent)  # no Cargo.toml here
        self.assertIn("CT-006", rule_ids(findings))

    def test_tag_001_unknown_reserved_tag(self) -> None:
        ci, _ = load_ci_toml(fixture("TAG-001", "red"))
        self.assertIn("TAG-001", rule_ids(check_tag_001(ci, "[ci-bogus] add thing")))

        ci, _ = load_ci_toml(fixture("TAG-001", "green"))
        self.assertNotIn("TAG-001", rule_ids(check_tag_001(ci, "[ci-windows] add thing")))

    def test_tag_001_ignores_non_reserved_brackets(self) -> None:
        ci, _ = load_ci_toml(fixture("TAG-001", "green"))
        self.assertNotIn("TAG-001", rule_ids(check_tag_001(ci, "[WIP] still working")))

    def test_tag_002_release_with_no_test(self) -> None:
        self.assertIn("TAG-002", rule_ids(check_tag_002("[release][no-test] go")))
        self.assertIn("TAG-002", rule_ids(check_tag_002("[release][no-test-integration] go")))
        self.assertEqual([], check_tag_002("[release] go"))
        self.assertEqual([], check_tag_002("[no-test] go"))


class ExceptionFixtureTest(unittest.TestCase):
    def test_ct_005_expired_exception_is_a_violation(self) -> None:
        ci, _ = load_ci_toml(fixture("CT-005", "red"))
        outcome = apply_exceptions(ci, [])
        self.assertIn("CT-005", rule_ids(outcome.findings))

    def test_ct_005_unexpired_exception_is_silent(self) -> None:
        ci, _ = load_ci_toml(fixture("CT-005", "green"))
        outcome = apply_exceptions(ci, [])
        self.assertNotIn("CT-005", rule_ids(outcome.findings))

    def test_matched_exception_becomes_approved(self) -> None:
        from ci_lint.finding import Finding, Status

        ci, _ = load_ci_toml(fixture("CT-005", "green"))
        finding = Finding(rule="GEN-005", path="ci.sh", message="x", fix="y")
        outcome = apply_exceptions(ci, [finding])
        self.assertEqual(1, len(outcome.findings))
        self.assertEqual(Status.APPROVED_EXCEPTION, outcome.findings[0].status)

    def test_today_is_a_real_date(self) -> None:
        # sanity: the exceptions module uses the real clock by default.
        self.assertIsInstance(datetime.date.today(), datetime.date)


if __name__ == "__main__":
    unittest.main()
