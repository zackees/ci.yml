"""Source guard classification does not provide a blanket optional-step waiver."""

import copy
import unittest
from dataclasses import replace

from ci_lint.workflow_replay_minimal_skip import classify_minimal_skip
from ci_lint.workflow_replay import ReplayJob, prove_replay
from ci_lint.workflow_replay_config import DeclaredReplayJob, _job
from ci_lint.workflow_replay_static import _check_job
from ci_lint.tests import test_workflow_replay as replay_fixtures


class MinimalSkipSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.job = {"steps": [
            {"id": "mode", "name": "Select mode", "run": "select-mode"},
            {"name": "Full tests", "if": "steps.mode.outputs.mode == 'full'", "run": "full-tests"},
        ]}

    def test_full_only_step_names_its_required_producer(self) -> None:
        proof = classify_minimal_skip(self.job, "Full tests")
        self.assertIsNotNone(proof)
        self.assertEqual(proof.producer, "Select mode")
        self.assertFalse(proof.failure_handler)

    def test_unknown_or_minimal_conditions_are_not_optional(self) -> None:
        for condition in ("false", "steps.mode.outputs.mode != 'full'", "steps.other.outputs.mode == 'full'",
                          "steps.mode.outputs.mode == 'full' || true", "failure()"):
            job = copy.deepcopy(self.job)
            job["steps"][1]["if"] = condition
            with self.subTest(condition=condition):
                self.assertIsNone(classify_minimal_skip(job, "Full tests"))

    def test_missing_duplicate_late_or_conditional_producer_is_unproven(self) -> None:
        for mutation in ("missing", "duplicate", "late", "conditional"):
            job = copy.deepcopy(self.job)
            if mutation == "missing":
                job["steps"].pop(0)
            elif mutation == "duplicate":
                job["steps"].append(copy.deepcopy(job["steps"][0]))
            elif mutation == "late":
                job["steps"].reverse()
            else:
                job["steps"][0]["if"] = "false"
            with self.subTest(mutation=mutation):
                self.assertIsNone(classify_minimal_skip(job, "Full tests"))

    def test_only_the_exact_failure_cancel_command_is_classified(self) -> None:
        job = copy.deepcopy(self.job)
        job["steps"][1].update({"if": "failure() && github.event_name == 'pull_request' && steps.mode.outputs.mode != 'full'",
                                 "run": "gh run cancel ${{ github.run_id }}"})
        self.assertTrue(classify_minimal_skip(job, "Full tests").failure_handler)
        job["steps"][1]["run"] = "run-tests"
        self.assertIsNone(classify_minimal_skip(job, "Full tests"))

    def test_static_binding_rejects_changed_guard_or_producer_and_full_mode(self) -> None:
        declared = DeclaredReplayJob("ci.yml:tests", ReplayJob(
            "CI/Tests", ("Select mode", "Full tests"), minimal_skip_steps=("Full tests",),
            minimal_mode_step="Select mode"), ())
        self.assertEqual(_check_job(declared, self.job, "ci.yml"), [])
        self.assertTrue(_check_job(declared, self.job, "ci.yml", mode="full"))
        invalid = replace(declared, proof=replace(declared.proof, minimal_mode_step="Another producer"))
        self.assertTrue(_check_job(invalid, self.job, "ci.yml"))
        job = copy.deepcopy(self.job)
        job["steps"][1]["if"] = "false"
        self.assertTrue(_check_job(declared, job, "ci.yml"))

    def test_config_requires_distinct_exclusions_and_nonoptional_producer(self) -> None:
        raw = {"source-job": "ci.yml:tests", "key": "CI/Tests",
               "steps": ["Select mode", "Full tests"], "minimal-skip-steps": ["Full tests"],
               "minimal-mode-step": "Select mode"}
        findings = []
        self.assertIsNotNone(_job(raw, source="ci.toml", path="job", findings=findings))
        self.assertEqual(findings, [])
        for changes in ({"minimal-mode-step": "Full tests"}, {"minimal-mode-step": ""},
                        {"minimal-skip-steps": ["Full tests", "Full tests"]},
                        {"cache-save-steps": ["Full tests"]}):
            findings = []
            self.assertIsNone(_job({**raw, **changes}, source="ci.toml", path="job", findings=findings))
            self.assertTrue(findings)


class MinimalSkipRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        fixture = replay_fixtures.WorkflowReplayTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.raw = fixture.raw
        self.expected = replace(fixture.expected, required_jobs=(ReplayJob(
            "CI/Tests", ("Run tests", "Full tests"), minimal_skip_steps=("Full tests",),
            minimal_mode_step="Run tests"),))
        self.sections = self.raw["tree"]["groups"][0]["jobs"][0]["sections"]
        self.sections.append({"name": "Full tests", "stage": "Main", "status": "completed",
                              "conclusion": "skipped", "first_seq": None, "last_seq": None})

    def test_explicit_skip_and_executed_producer_are_required(self) -> None:
        self.assertEqual(prove_replay(self.raw, self.expected).jobs, ("CI/Tests",))
        for conclusion in ("success", "failure", "cancelled"):
            self.sections[-1]["conclusion"] = conclusion
            with self.subTest(conclusion=conclusion), self.assertRaises(ValueError):
                prove_replay(self.raw, self.expected)
        self.sections[-1]["conclusion"] = "skipped"
        self.sections[0]["conclusion"] = "skipped"
        with self.assertRaises(ValueError):
            prove_replay(self.raw, self.expected)

    def test_missing_duplicate_and_post_only_skip_are_rejected(self) -> None:
        skip = self.sections.pop()
        with self.assertRaises(ValueError):
            prove_replay(self.raw, self.expected)
        self.sections.extend([skip, copy.deepcopy(skip)])
        with self.assertRaises(ValueError):
            prove_replay(self.raw, self.expected)
        self.sections.pop()
        skip["stage"] = "Post"
        with self.assertRaises(ValueError):
            prove_replay(self.raw, self.expected)

    def test_full_and_dispatch_selection_cannot_use_minimal_exclusions(self) -> None:
        for expected in (replace(self.expected, mode="full"),
                         replace(self.expected, trigger="workflow_dispatch", event="workflow_dispatch")):
            with self.assertRaises(ValueError):
                prove_replay(self.raw, expected)
