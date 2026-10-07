"""Derive complete per-execution checks from source and typed caller bindings."""

import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ci_lint.workflow_replay_checks import derive_checks
from ci_lint.workflow_replay_expansion import ExpandedJob
from ci_lint.workflow_replay_identity import identity_part
from ci_lint.workflow_replay_inputs import BoundInput
from ci_lint.workflow_replay import ReplayJob, _prove_job
from ci_lint.workflow_replay_config import DeclaredReplayJob, ReplayConfig
from ci_lint.workflow_replay_static import check_replay_static
from ci_lint.tests.test_workflow_replay_expansion import workflow


class DerivedChecksTest(unittest.TestCase):
    def setUp(self):
        self.job = {"steps": [
            {"name": "Compile", "run": "build", "if": "inputs.compile"},
            {"name": "Clippy", "run": "lint", "if": "inputs.clippy"},
            {"name": "Run ${{ inputs.suite }}", "run": "tests"},
            {"name": "Failure log", "run": "diagnostic", "if": "failure()"},
        ]}
        self.expanded = ExpandedJob("build.yml:build", "build/build", ".github/workflows/build.yml", {}, self.job,
                                    (BoundInput("compile", True), BoundInput("clippy", False), BoundInput("suite", "unit")),
                                    (identity_part("build", None),))

    def test_checks_and_exclusions_follow_each_actual_profile(self):
        proof = derive_checks(self.expanded, mode="minimal", event="pull_request")
        self.assertEqual(proof.steps, ("Compile", "Clippy", "Run unit", "Failure log"))
        self.assertEqual(proof.input_skip_steps, ("Clippy", "Failure log"))
        other = replace(self.expanded, inputs=(BoundInput("compile", False), BoundInput("clippy", True), BoundInput("suite", "unit")))
        self.assertEqual(derive_checks(other, mode="minimal", event="pull_request").input_skip_steps, ("Compile", "Failure log"))

    def test_unknown_condition_is_mandatory_and_unknown_name_rejects(self):
        self.job["steps"][0]["if"] = "github.ref == 'main'"
        proof = derive_checks(self.expanded, mode="minimal", event="pull_request")
        self.assertNotIn("Compile", proof.input_skip_steps)
        self.job["steps"][0]["name"] = "${{ github.ref }}"
        with self.assertRaises(ValueError):
            derive_checks(self.expanded, mode="minimal", event="pull_request")

    def test_missing_duplicate_and_empty_check_names_reject(self):
        for name in (None, "", "Clippy"):
            with self.subTest(name=name):
                self.job["steps"][0]["name"] = name
                with self.assertRaises(ValueError):
                    derive_checks(self.expanded, mode="minimal", event="pull_request")

    def test_static_check_uses_shared_source_derivation(self):
        document = workflow(".github/workflows/ci.yml", {"jobs": {"build": {
            "steps": [{"name": "Build", "run": "build"}]}}})
        config = ReplayConfig("owner/repo", document.path, "minimal", (
            DeclaredReplayJob("ci.yml:build", ReplayJob("ci.yml:build", ()), (), derive_checks=True),), qualified=True)
        with patch("ci_lint.workflow_replay_static.load_workflows", return_value=(document,)):
            self.assertEqual(check_replay_static(config, Path("/unused")), [])
            document.document["jobs"]["build"]["steps"][0].pop("name")
            self.assertTrue(check_replay_static(config, Path("/unused")))

    def test_event_excluded_job_requires_explicit_empty_skip(self):
        self.job["if"] = "github.event_name == 'push' && github.ref == 'refs/heads/main'"
        proof = derive_checks(self.expanded, mode="minimal", event="pull_request")
        self.assertTrue(proof.excluded)
        self.assertEqual(proof.steps, ())
        raw = {"job_id": "build", "matrix": None, "status": "completed",
               "conclusion": "skipped", "sections": []}
        _prove_job(raw, proof)
        for altered in ({**raw, "conclusion": "success"}, {**raw, "status": "pending"},
                        {**raw, "sections": [{"stage": "Main", "conclusion": "success"}]},
                        {**raw, "job_id": "other"}):
            with self.assertRaises(ValueError):
                _prove_job(altered, proof)

    def test_unknown_or_unbound_job_guard_never_waives_execution(self):
        for guard in ("false", "github.ref == 'refs/heads/main'", "failure()"):
            self.job["if"] = guard
            proof = derive_checks(self.expanded, mode="minimal", event="pull_request")
            self.assertFalse(proof.excluded)
            self.assertTrue(proof.steps)
        self.job["if"] = "github.event_name == 'pull_request'"
        self.assertFalse(derive_checks(self.expanded, mode="minimal", event="pull_request").excluded)
