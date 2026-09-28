"""SEC-001, SEC-002 -- ci_lint/rules/secrets_rules.py.

SEC-001 is a raw-text scan (works without PyYAML/yq); SEC-002 needs the
parsed job/permissions structure.
"""

from __future__ import annotations

import unittest

from ci_lint.rules.secrets_rules import check_sec_001, check_sec_002
from ci_lint.tests.helpers import fixture, requires_yaml_tooling


class SecretsFixtureTest(unittest.TestCase):
    def test_sec_001_forbidden_secret_reference(self) -> None:
        rules = [f.rule for f in check_sec_001(fixture("SEC-001", "red"))]
        self.assertIn("SEC-001", rules)
        rules = [f.rule for f in check_sec_001(fixture("SEC-001", "green"))]
        self.assertNotIn("SEC-001", rules)

    def test_sec_001_github_token_is_allowed(self) -> None:
        rules = [f.rule for f in check_sec_001(fixture("SEC-001", "green"))]
        self.assertEqual([], rules)

    @requires_yaml_tooling
    def test_sec_002_wide_permissions(self) -> None:
        rules = [f.rule for f in check_sec_002(fixture("SEC-002", "red"))]
        self.assertIn("SEC-002", rules)
        rules = [f.rule for f in check_sec_002(fixture("SEC-002", "green"))]
        self.assertNotIn("SEC-002", rules)


if __name__ == "__main__":
    unittest.main()
