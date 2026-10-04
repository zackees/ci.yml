"""Selected replays must prove their entire dependency closure."""

import unittest

from ci_lint.finding import Status
from ci_lint.workflow_replay import ReplayJob
from ci_lint.workflow_replay_config import DeclaredReplayJob, ReplayConfig, ReplaySelection
from ci_lint.workflow_replay_dependencies import check_selection_dependencies, dependency_closure


class ReplayDependenciesTest(unittest.TestCase):
    def test_recursive_dependencies(self) -> None:
        proof = dependency_closure({"jobs": {"verify": {}, "select": {"needs": "verify"},
                                            "linux": {"needs": ["select", "verify"]}}}, "linux")
        self.assertIsNone(proof.problem)
        self.assertEqual(proof.jobs, ("linux", "select", "verify"))

    def test_ambiguous_or_invalid_graph_is_unproven(self) -> None:
        for document in (
            {"jobs": {"linux": {"needs": "${{ inputs.job }}"}}},
            {"jobs": {"linux": {"needs": "missing"}}},
            {"jobs": {"linux": {"needs": "linux"}}},
            {"jobs": {"linux": {"needs": ["verify", False]}, "verify": {}}},
        ):
            with self.subTest(document=document):
                self.assertIsNotNone(dependency_closure(document, "linux").problem)

    def test_omitted_dependency_and_unreachable_claim(self) -> None:
        config = ReplayConfig("owner/repo", ".github/workflows/ci.yml", "minimal", (
            DeclaredReplayJob("ci.yml:linux", ReplayJob("CI/Linux", ("Tests",)), ("tests",)),
            DeclaredReplayJob("ci.yml:extra", ReplayJob("CI/Extra", ("Tests",)), ("tests",)),
        ), (ReplaySelection("tests", "linux", "workflow_dispatch", ()),))
        findings = check_selection_dependencies(config, {"jobs": {"linux": {"needs": "verify"}, "verify": {}, "extra": {}}})
        self.assertEqual(findings[0].status, Status.VIOLATION)
        self.assertIn("omitted=['verify']", findings[0].message)
        self.assertIn("unreachable=['extra']", findings[0].message)
