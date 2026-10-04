"""PR-only cache-write exclusions cannot waive validation or other events."""

import copy
import unittest
from dataclasses import replace

from ci_lint.workflow_replay_cache_save import approved_pr_cache_save
from ci_lint.workflow_replay import ReplayJob, prove_replay
from ci_lint.workflow_replay_config import DeclaredReplayJob, ReplayConfig, ReplaySelection, _job
from ci_lint.workflow_replay_static import _check_job, _check_pr_selections
from ci_lint.tests import test_workflow_replay as replay_fixtures


class PrSaveSourceTest(unittest.TestCase):
    def setUp(self):
        self.job = {"steps": [
            {"name": "Run tests", "run": "run-tests"},
            {"name": "Save cache", "uses": "actions/cache/save@v4",
             "if": "github.event_name != 'pull_request'",
             "with": {"path": "cache/units", "key": "units-v1"}},
        ]}

    def test_only_finite_non_pr_guards_are_recognized(self):
        for guard in ("github.event_name != 'pull_request'",
                      "github.ref == 'refs/heads/main'",
                      "${{ github.ref == 'refs/heads/main' && steps.units.outputs.cache-hit != 'true' }}"):
            self.job["steps"][1]["if"] = guard
            self.assertTrue(approved_pr_cache_save(self.job, "Save cache"))
        for guard in ("false", "github.event_name == 'pull_request'",
                      "github.ref != 'refs/heads/main'", "github.event_name != 'pull_request' || true",
                      "github.ref == 'refs/heads/main' && run_tests()"):
            self.job["steps"][1]["if"] = guard
            self.assertFalse(approved_pr_cache_save(self.job, "Save cache"))

    def test_actions_validation_and_empty_inputs_fail_closed(self):
        for change in ({"run": "run-tests"}, {"uses": "owner/cache/save@v4"},
                       {"uses": "actions/cache/restore@v4"},
                       {"with": {"path": "", "key": "units-v1"}},
                       {"with": {"path": "units", "key": ""}}):
            job = copy.deepcopy(self.job)
            job["steps"][1].update(change)
            self.assertFalse(approved_pr_cache_save(job, "Save cache"))
        self.job["steps"].append(copy.deepcopy(self.job["steps"][1]))
        self.assertFalse(approved_pr_cache_save(self.job, "Save cache"))

    def test_config_and_static_binding_are_strict(self):
        raw = {"source-job": "ci.yml:tests", "key": "CI/Tests",
               "steps": ["Run tests", "Save cache"], "pr-cache-save-steps": ["Save cache"]}
        findings = []
        declared = _job(raw, source="ci.toml", path="job", findings=findings)
        self.assertIsNotNone(declared)
        self.assertEqual(findings, [])
        self.assertEqual(_check_job(declared, self.job, "ci.yml"), [])
        invalid = DeclaredReplayJob("ci.yml:tests", ReplayJob(
            "CI/Tests", ("Run tests", "Save cache"), pr_cache_save_steps=("Run tests",)), ())
        self.assertTrue(_check_job(invalid, self.job, "ci.yml"))
        for change in ({"pr-cache-save-steps": ["Save cache", "Save cache"]},
                       {"pr-cache-save-steps": ["Unknown"]}, {"cache-save-steps": ["Save cache"]}):
            findings = []
            self.assertIsNone(_job({**raw, **change}, source="ci.toml", path="job", findings=findings))

    def test_dispatch_selection_is_rejected_before_execution(self):
        declared = DeclaredReplayJob("ci.yml:tests", ReplayJob(
            "CI/Tests", ("Run tests", "Save cache"), pr_cache_save_steps=("Save cache",)), ("tests",))
        config = ReplayConfig("owner/repo", ".github/workflows/ci.yml", "full", (declared,),
                              (ReplaySelection("tests", "tests", "workflow_dispatch", ()),))
        self.assertTrue(_check_pr_selections(config))
        self.assertEqual(_check_pr_selections(replace(config, selections=(
            ReplaySelection("tests", "tests", "pull_request", ()),))), [])


class PrSaveRuntimeTest(unittest.TestCase):
    def setUp(self):
        fixture = replay_fixtures.WorkflowReplayTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.raw = fixture.raw
        self.expected = replace(fixture.expected, required_jobs=(ReplayJob(
            "CI/Tests", ("Run tests", "Save cache"), pr_cache_save_steps=("Save cache",)),))
        self.sections = self.raw["tree"]["groups"][0]["jobs"][0]["sections"]
        self.sections.append({"name": "Save cache", "stage": "Main", "status": "completed",
                              "conclusion": "skipped", "first_seq": None, "last_seq": None})

    def test_only_explicit_skipped_main_in_pr_is_accepted(self):
        self.assertEqual(prove_replay(self.raw, self.expected).jobs, ("CI/Tests",))
        self.raw["mode"] = "full"
        self.assertEqual(prove_replay(self.raw, replace(self.expected, mode="full")).jobs, ("CI/Tests",))
        self.raw.update(event="workflow_dispatch", trigger="workflow_dispatch")
        with self.assertRaises(ValueError):
            prove_replay(self.raw, replace(self.expected, mode="full", event="workflow_dispatch",
                                           trigger="workflow_dispatch"))

    def test_success_missing_failed_duplicate_post_and_skipped_validation_reject(self):
        for conclusion in ("success", "failure", "cancelled"):
            self.sections[-1]["conclusion"] = conclusion
            with self.assertRaises(ValueError):
                prove_replay(self.raw, self.expected)
        self.sections[-1]["conclusion"] = "skipped"
        save = self.sections.pop()
        with self.assertRaises(ValueError):
            prove_replay(self.raw, self.expected)
        self.sections.extend([save, copy.deepcopy(save)])
        with self.assertRaises(ValueError):
            prove_replay(self.raw, self.expected)
        self.sections.pop()
        save["stage"] = "Post"
        with self.assertRaises(ValueError):
            prove_replay(self.raw, self.expected)
        save["stage"] = "Main"
        self.sections[0]["conclusion"] = "skipped"
        with self.assertRaises(ValueError):
            prove_replay(self.raw, self.expected)
