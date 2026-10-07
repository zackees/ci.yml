"""One full receipt proves the union of selected lanes, including prerequisites."""

import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ci_lint.tests.test_workflow_replay_expansion import workflow
from ci_lint.workflow_replay import ReplayInput, ReplayJob
from ci_lint.workflow_replay_config import DeclaredReplayJob, ReplayConfig, ReplaySelection
from ci_lint.workflow_replay_plan import build_plan


class FullReplayPlanTest(unittest.TestCase):
    def setUp(self):
        self.file = workflow(".github/workflows/ci.yml", {"jobs": {
            "prepare": {"steps": [{"name": "Prepare", "run": "prepare"}]},
            "lint": {"needs": "prepare", "steps": [{"name": "Lint", "run": "lint"}]},
            "tests": {"needs": "prepare", "steps": [{"name": "Test", "run": "test"}]},
        }})
        self.config = ReplayConfig("owner/repo", self.file.path, "minimal", (
            DeclaredReplayJob("ci.yml:prepare", ReplayJob("prepare", ()), ("lint", "tests"), True),
            DeclaredReplayJob("ci.yml:lint", ReplayJob("lint", ()), ("lint",), True),
            DeclaredReplayJob("ci.yml:tests", ReplayJob("tests", ()), ("tests",), True),
        ), (ReplaySelection("lint", "lint", "pull_request", ()),
            ReplaySelection("tests", "tests", "pull_request", ())), "stdout", True)

    def plan(self, config=None, lane=None):
        with patch("ci_lint.workflow_replay_plan.load_workflows", return_value=(self.file,)):
            return build_plan(Path("/unused"), config or self.config, lane=lane)

    def test_full_plan_requires_union_and_shared_prerequisite_once(self):
        plan = self.plan()
        self.assertIsNone(plan.selected)
        self.assertEqual(plan.lanes, ("lint", "tests"))
        self.assertEqual({job.identity[-1].job_id for job in plan.required}, {"prepare", "lint", "tests"})
        self.assertEqual(len(plan.required), 3)
        narrow = self.plan(lane="tests")
        self.assertEqual(narrow.selected, "tests")
        self.assertEqual(len(narrow.required), 2)
        self.assertEqual(narrow.lanes, ())

    def test_mixed_invocations_and_legacy_full_selection_refuse(self):
        for selection in (replace(self.config.selections[1], event="workflow_dispatch"),
                          replace(self.config.selections[1], inputs=(ReplayInput("tier", "test"),)),
                          replace(self.config.selections[1], workflow=".github/workflows/other.yml")):
            with self.assertRaises(ValueError):
                self.plan(replace(self.config, selections=(self.config.selections[0], selection)))
        with self.assertRaises(ValueError):
            self.plan(replace(self.config, qualified=False))

    def test_missing_prerequisite_declaration_and_unknown_lane_refuse(self):
        with self.assertRaises(ValueError):
            self.plan(replace(self.config, jobs=self.config.jobs[1:]))
        with self.assertRaises(ValueError):
            self.plan(lane="missing")
        with self.assertRaises(ValueError):
            self.plan(replace(self.config, selections=self.config.selections[:1]))

    def test_all_event_excluded_selection_cannot_prove_a_lane(self):
        for job in self.file.document["jobs"].values():
            job["if"] = "github.event_name == 'push'"
        with self.assertRaisesRegex(ValueError, "no required executed job"):
            self.plan(lane="tests")
