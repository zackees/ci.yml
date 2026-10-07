"""Explicit public output requests are declared once and bound to source identities."""

import copy
import json
import sys
import unittest

from ci_lint.tests import test_workflow_replay as fixtures
from ci_lint.tests.helpers import requires_yaml_tooling
from ci_lint.workflow_replay_config import ReplayConfig, DeclaredReplayJob
from ci_lint.workflow_replay_runtime import run_checked_command
from ci_lint.workflow_replay_plan import build_plan
from ci_lint.workflow_replay_capture import request_arguments
from ci_lint.workflow_replay_config import _job


class OutputRequestTest(unittest.TestCase):
    def test_output_capture_declaration_loads_without_repeating_runner_flags(self):
        findings = []
        declared = _job({"source-job": "ci.yml:plan", "capture-outputs": ["matrix"]},
                        source="local-gate.toml", path="gate.replay.jobs[0]",
                        findings=findings, qualified=True)
        self.assertEqual(findings, [])
        self.assertEqual(declared.capture_outputs, ("matrix",))

    def test_invalid_duplicate_or_unqualified_capture_declarations_refuse(self):
        for names, qualified in ((["matrix", "matrix"], True), (["bad:name"], True), (["matrix"], False)):
            findings = []
            _job({"source-job": "ci.yml:plan", "key": "plan", "steps": ["Plan"], "capture-outputs": names},
                 source="gate.toml", path="gate.replay.jobs[0]", findings=findings, qualified=qualified)
            self.assertTrue(findings)

    def test_conflicting_manual_selectors_and_other_adapter_shape_refuse(self):
        for argv in (("runner", "run"), ("bosn", "ci", "run", "--ci-output", "plan:secret"),
                     ("bosn", "ci", "run", "--ci-output=plan:secret")):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                request_arguments(argv, ("plan:matrix",))
        self.assertEqual(request_arguments(("bosn", "ci", "run"), ()), ("bosn", "ci", "run"))

    @requires_yaml_tooling
    def test_real_child_receives_the_source_bound_request_and_missing_evidence_refuses(self):
        fixture = fixtures.WorkflowReplayTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        repo = fixture.workspace
        workflows = repo / ".github/workflows"
        workflows.mkdir(parents=True)
        source = {"jobs": {"tests": {"outputs": {"matrix": "${{ steps.plan.outputs.matrix }}"},
                                     "steps": [{"name": "Run tests", "run": "tests"}]}}}
        (workflows / "ci.yml").write_text(json.dumps(source))
        config = ReplayConfig("owner/repo", fixture.expected.workflow, "minimal", (
            DeclaredReplayJob("ci.yml:tests", fixture.expected.required_jobs[0], (), True, ("matrix",)),
        ), qualified=True, report_source="stdout")
        plan = build_plan(repo, config, defer_outputs=True)
        self.assertEqual(plan.capture_requests, ("tests:matrix",))
        raw = copy.deepcopy(fixture.raw)
        job = raw["tree"]["groups"][0]["jobs"][0]
        job.update(job_id="tests", matrix=None, identity=[{"jobID": "tests", "matrix": None}],
                   output_evidence={"schema_version": 1, "seq": 21, "values": {"matrix": "[]"}, "error": None})
        report, args = repo / "report.json", repo / "argv.json"
        child = repo / "bosn"
        child.write_text("#!" + sys.executable + "\nimport json,sys\nfrom pathlib import Path\n" +
                         "Path(" + repr(str(args)) + ").write_text(json.dumps(sys.argv[1:]))\n" +
                         "print(Path(" + repr(str(report)) + ").read_text())\n")
        child.chmod(0o755)
        for evidence, accepted in ((job["output_evidence"], True), (None, False)):
            job["output_evidence"] = evidence
            report.write_text(json.dumps(raw))
            outcome = run_checked_command(repo, (str(child), "ci", "run"), config,
                                          head=fixture.expected.sha, tree=fixture.expected.git_tree)
            self.assertEqual(outcome.returncode == 0, accepted, outcome.error)
            self.assertEqual(json.loads(args.read_text()), ["ci", "run", "--ci-output", "tests:matrix"])
        (workflows / "ci.yml").write_text(json.dumps({"jobs": {"tests": {"steps": source["jobs"]["tests"]["steps"]}}}))
        args.write_text("not executed")
        outcome = run_checked_command(repo, (str(child), "ci", "run"), config,
                                      head=fixture.expected.sha, tree=fixture.expected.git_tree)
        self.assertEqual(outcome.returncode, 1)
        self.assertEqual(args.read_text(), "not executed")
