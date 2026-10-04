"""Strict declarations for workflow execution proof; not execution evidence."""

from __future__ import annotations

import tomllib
import unittest

from ci_lint.finding import Finding
from ci_lint.workflow_replay_config import parse_replay

DECLARATION = '''
repository = "owner/repo"
workflow = "ci.yml"
mode = "minimal"
[[jobs]]
source-job = "ci.yml:tests"
key = "CI/Tests"
steps = ["Run tests"]
lanes = ["tests"]
'''


class ReplayDeclarationTest(unittest.TestCase):
    def test_strict_declaration_keeps_coverage_and_execution_names(self) -> None:
        findings: list[Finding] = []
        config = parse_replay(tomllib.loads(DECLARATION), source="local-gate.toml",
                              path="gate.replay", findings=findings)
        self.assertEqual(findings, [])
        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual(config.workflow, ".github/workflows/ci.yml")
        self.assertEqual(config.jobs[0].source_job, "ci.yml:tests")
        self.assertEqual(config.jobs[0].proof.steps, ("Run tests",))

    def test_unknown_keys_and_empty_coverage_are_rejected(self) -> None:
        for document in (
            DECLARATION + 'bogus = true\n',
            DECLARATION.replace('steps = ["Run tests"]', 'steps = []'),
            DECLARATION.replace('source-job = "ci.yml:tests"', 'source-job = "../ci.yml:tests"'),
            DECLARATION.replace('repository = "owner/repo"', 'repository = ""'),
            DECLARATION.replace('mode = "minimal"', 'mode = "unknown"'),
            DECLARATION + '[[jobs]]\nsource-job="ci.yml:tests"\nkey="CI/Tests"\nsteps=["Run tests"]\nlanes=["tests"]\n',
        ):
            with self.subTest(document=document):
                findings: list[Finding] = []
                self.assertIsNone(parse_replay(tomllib.loads(document), source="local-gate.toml",
                                              path="gate.replay", findings=findings))
                self.assertTrue(findings)

    def test_lane_dispatch_selection_keeps_exact_tier(self) -> None:
        document = DECLARATION + '\n[[selections]]\nlane="tests"\njob="tests"\nevent="workflow_dispatch"\ninputs={tier="test"}\n'
        findings: list[Finding] = []
        config = parse_replay(tomllib.loads(document), source="local-gate.toml", path="gate.replay", findings=findings)
        self.assertEqual(findings, [])
        assert config is not None
        self.assertEqual(config.selections[0].inputs[0].value, "test")
        for invalid in (document.replace('lane="tests"', 'lane="unknown"'),
                        document.replace('event="workflow_dispatch"', 'event="push"'),
                        document + '\n[[selections]]\nlane="tests"\njob="tests"\nevent="workflow_dispatch"\n'):
            with self.subTest(document=invalid):
                rejected: list[Finding] = []
                self.assertIsNone(parse_replay(tomllib.loads(invalid), source="local-gate.toml", path="gate.replay", findings=rejected))
