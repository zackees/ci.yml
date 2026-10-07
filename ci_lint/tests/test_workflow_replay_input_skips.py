"""An empty bound input can exclude only finite source-guarded steps."""

import copy
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ci_lint.workflow_replay import ReplayJob, prove_replay
from ci_lint.workflow_replay_config import DeclaredReplayJob, ReplayConfig, ReplaySelection, _job
from ci_lint.workflow_replay_input_skips import excluded_input_steps
from ci_lint.workflow_replay_inputs import BoundInput
from ci_lint.workflow_replay_static import check_replay_static
from ci_lint.tests import test_workflow_replay as fixtures
from ci_lint.tests.test_workflow_replay_expansion import workflow


class InputSkipSourceTest(unittest.TestCase):
    def setUp(self):
        self.job = {"steps": [
            {"name": "Download", "id": "download", "if": "inputs.artifact != ''", "uses": "actions/download-artifact@v4"},
            {"name": "Verify", "id": "verified", "if": "steps.download.outcome == 'success'", "run": "verify"},
            {"name": "Build", "if": "steps.verified.outcome != 'success'", "run": "build"},
        ]}

    def test_empty_input_excludes_download_and_dependent_success_only(self):
        self.assertEqual(excluded_input_steps(self.job, (BoundInput("artifact", ""),)), ("Download", "Verify"))
        self.assertEqual(excluded_input_steps(self.job, (BoundInput("artifact", "payload"),)), ())
        self.assertEqual(excluded_input_steps(self.job, ()), ())

    def test_negated_success_guard_keeps_unknown_and_fallback_checks_required(self):
        self.job["steps"][0]["if"] = "!cancelled() && !failure() && !inputs.build"
        inputs = (BoundInput("build", True),)
        self.assertEqual(excluded_input_steps(self.job, inputs, successful=True), ("Download", "Verify"))
        self.assertEqual(excluded_input_steps(self.job, inputs), ("Download", "Verify"))
        self.assertEqual(excluded_input_steps(self.job, (), successful=True), ())
        self.assertEqual(excluded_input_steps(self.job, (BoundInput("build", False),), successful=True), ())
        self.job["steps"][0]["if"] = "!success() || !inputs.build"
        self.assertEqual(excluded_input_steps(self.job, inputs), ())
        self.assertEqual(excluded_input_steps(self.job, inputs, successful=True), ("Download", "Verify"))

    def test_unknown_or_literal_false_conditions_are_not_waivers(self):
        for guard in ("false", "inputs.artifact == ''", "inputs.artifact != '' || true",
                      "inputs.other != ''", "steps.missing.outcome == 'success'"):
            job = copy.deepcopy(self.job)
            job["steps"][0]["if"] = guard
            self.assertEqual(excluded_input_steps(job, (BoundInput("artifact", ""),)), ())

    def test_late_or_ambiguous_producers_do_not_prove_dependent_skip(self):
        self.job["steps"][0], self.job["steps"][1] = self.job["steps"][1], self.job["steps"][0]
        self.assertEqual(excluded_input_steps(self.job, (BoundInput("artifact", ""),)), ("Download",))
        self.job["steps"][2]["id"] = "download"
        self.assertEqual(excluded_input_steps(self.job, (BoundInput("artifact", ""),)), ("Download",))

    def test_static_binding_uses_real_caller_and_default(self):
        entry = workflow(".github/workflows/ci.yml", {"name": "CI", "jobs": {
            "linux": {"uses": "./.github/workflows/check.yml"}}})
        called = workflow(".github/workflows/check.yml", {"name": "Check", "on": {
            "workflow_call": {"inputs": {"artifact": {"type": "string", "default": ""}}}},
            "jobs": {"check": {"name": "Check", **self.job}}})
        declared = DeclaredReplayJob("check.yml:check", ReplayJob(
            "linux/Check/Check", ("Download", "Verify", "Build"), input_skip_steps=("Download", "Verify")), ("tests",))
        config = ReplayConfig("owner/repo", entry.path, "minimal", (declared,),
                              (ReplaySelection("tests", "linux", "pull_request", ()),))
        with patch("ci_lint.workflow_replay_static.load_workflows", return_value=(entry, called)):
            self.assertEqual(check_replay_static(config, Path("/unused")), [])
            for value in ("payload", "${{ github.ref }}"):
                entry.document["jobs"]["linux"]["with"] = {"artifact": value}
                self.assertTrue(check_replay_static(config, Path("/unused")))

    def test_config_rejects_overlap_duplicate_and_unknown_exclusions(self):
        raw = {"source-job": "ci.yml:check", "key": "CI/Check", "steps": ["Build", "Download"],
               "input-skip-steps": ["Download"]}
        findings = []
        self.assertIsNotNone(_job(raw, source="ci.toml", path="job", findings=findings))
        self.assertEqual(findings, [])
        for change in ({"input-skip-steps": ["Download", "Download"]},
                       {"input-skip-steps": ["Unknown"]}, {"cache-save-steps": ["Download"]},
                       {"pr-cache-save-steps": ["Download"]}):
            findings = []
            self.assertIsNone(_job({**raw, **change}, source="ci.toml", path="job", findings=findings))


class InputSkipRuntimeTest(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.WorkflowReplayTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.raw = fixture.raw
        self.expected = replace(fixture.expected, required_jobs=(ReplayJob(
            "CI/Tests", ("Run tests", "Download"), input_skip_steps=("Download",)),))
        self.sections = self.raw["tree"]["groups"][0]["jobs"][0]["sections"]
        self.sections.append({"name": "Download", "stage": "Main", "status": "completed",
                              "conclusion": "skipped", "first_seq": None, "last_seq": None})

    def test_only_explicit_skipped_main_is_accepted(self):
        self.assertEqual(prove_replay(self.raw, self.expected).jobs, ("CI/Tests",))
        for change in ({"conclusion": "success"}, {"conclusion": "failure"}, {"stage": "Post"}, {"status": "queued"}):
            raw = copy.deepcopy(self.raw)
            raw["tree"]["groups"][0]["jobs"][0]["sections"][-1].update(change)
            with self.assertRaises(ValueError):
                prove_replay(raw, self.expected)
        self.sections.pop()
        with self.assertRaises(ValueError):
            prove_replay(self.raw, self.expected)

    def test_invalid_overlapping_declarations_cannot_skip_execution(self):
        job = self.expected.required_jobs[0]
        for changed in (replace(job, input_skip_steps=("Unknown",)),
                        replace(job, input_skip_steps=("Download", "Download")),
                        replace(job, cache_save_steps=("Download",))):
            with self.assertRaises(ValueError):
                prove_replay(self.raw, replace(self.expected, required_jobs=(changed,)))

    def test_duplicate_sections_and_skipped_validation_reject(self):
        self.sections.append(copy.deepcopy(self.sections[-1]))
        with self.assertRaises(ValueError):
            prove_replay(self.raw, self.expected)
        self.sections.pop()
        self.sections[0]["conclusion"] = "skipped"
        with self.assertRaises(ValueError):
            prove_replay(self.raw, self.expected)
