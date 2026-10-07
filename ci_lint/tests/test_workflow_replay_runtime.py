"""Real child processes exercise report handling; reports here are fixtures."""

from __future__ import annotations

import json
import copy
import sys
from dataclasses import replace
from unittest.mock import patch

from ci_lint.tests.test_workflow_replay import WorkflowReplayTest
from ci_lint.tests.helpers import requires_yaml_tooling
from ci_lint.workflow_replay_config import DeclaredReplayJob, ReplayConfig, ReplaySelection
from ci_lint.workflow_replay_runtime import _read_report, run_checked_command
from ci_lint.execution_pins import ExecutionPins


class ReplayRuntimeTest(WorkflowReplayTest):
    @requires_yaml_tooling
    def test_guard_only_deferral_refuses_old_provider_before_child(self) -> None:
        workflows = self.workspace / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text(
            "jobs:\n  plan:\n    steps: [{name: Plan, run: plan}]\n"
            "  tests:\n    needs: plan\n    steps:\n"
            "      - {name: Run tests, run: tests, if: \"needs.plan.outputs.test == 'true'\"}\n")
        config = replace(self.config(), qualified=True, report_source="stdout", jobs=tuple(
            DeclaredReplayJob(f"ci.yml:{name}", self.expected.required_jobs[0], ("tests",), True)
            for name in ("plan", "tests")))
        digest = "sha256:" + "a" * 64
        pins = ExecutionPins(1, "0.2.89-act2.12", digest, digest, digest, digest, digest)
        marker = self.workspace / "unexpected-execution"
        command = self.command(f"pathlib.Path({str(marker)!r}).write_text('ran')\nprint('{{}}')\n")
        with patch("ci_lint.workflow_replay_runtime.query_execution_pins", return_value=pins):
            outcome = run_checked_command(self.workspace, command, config,
                                          head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(outcome.returncode, 1)
        self.assertIn("act2.13", outcome.error)
        self.assertFalse(marker.exists())

    @requires_yaml_tooling
    def test_output_guard_without_matrix_requires_proved_planner(self) -> None:
        workflows = self.workspace / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text(
            "jobs:\n  plan:\n    outputs: {test: '${{ steps.plan.outputs.test }}', lanes: '${{ steps.plan.outputs.lanes }}'}\n"
            "    steps: [{name: Plan, run: plan}]\n"
            "  tests:\n    needs: plan\n    steps:\n"
            "      - {name: Run tests, run: tests}\n"
            "      - {name: Placeholder, run: placeholder, if: \"needs.plan.outputs.test != 'true'\"}\n")
        config = replace(self.config(), qualified=True, report_source="stdout", jobs=tuple(
            DeclaredReplayJob(f"ci.yml:{name}", self.expected.required_jobs[0], ("tests",), True)
            for name in ("plan", "tests")))
        original = self.raw["tree"]["groups"][0]["jobs"][0]
        jobs = []
        for identity, name in (("plan", "Plan"), ("tests", "Run tests")):
            job = copy.deepcopy(original)
            job.update(job_id=identity, matrix=None, identity=[{"jobID": identity, "matrix": None}])
            job["sections"][0]["name"] = name
            jobs.append(job)
        jobs[0]["output_evidence"] = {"schema_version": 1, "seq": 21,
                                       "values": {"test": "true"}, "error": None}
        jobs[1]["sections"].append({"name": "Placeholder", "stage": "Main", "status": "completed",
                                    "conclusion": "skipped", "first_seq": None, "last_seq": None})
        self.raw["tree"]["groups"][0]["jobs"] = jobs
        fixture = self.workspace / "guard.json"
        command = self.command(f"print(pathlib.Path({str(fixture)!r}).read_text())\n")
        fixture.write_text(json.dumps(self.raw))
        outcome = run_checked_command(self.workspace, command, config,
                                      head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(outcome.returncode, 0, outcome.error)
        for change in ({"values": {}}, {"error": "missing", "values": None}, {"seq": 19}):
            refused = copy.deepcopy(self.raw)
            refused["tree"]["groups"][0]["jobs"][0]["output_evidence"].update(change)
            fixture.write_text(json.dumps(refused))
            result = run_checked_command(self.workspace, command, config,
                                         head=self.expected.sha, tree=self.expected.git_tree)
            self.assertEqual(result.returncode, 1)
        jobs[0]["sections"][0]["conclusion"] = "skipped"
        fixture.write_text(json.dumps(self.raw))
        result = run_checked_command(self.workspace, command, config,
                                     head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(result.returncode, 1)
        jobs[0]["sections"][0]["conclusion"] = "success"
        jobs[0]["output_evidence"]["values"]["lanes"] = "[]"
        source = workflows / "ci.yml"
        source.write_text(source.read_text() +
                          "  platforms:\n    needs: plan\n"
                          "    if: needs.plan.outputs.lanes != '[]'\n"
                          "    strategy:\n      matrix: {lane: '${{ fromJSON(needs.plan.outputs.lanes) }}'}\n"
                          "    steps: [{name: Platform tests, run: platform-tests}]\n")
        config = replace(config, jobs=config.jobs + (
            DeclaredReplayJob("ci.yml:platforms", self.expected.required_jobs[0], ("tests",), True),))
        jobs.append({**copy.deepcopy(original), "job_id": "platforms", "matrix": None,
                     "identity": [{"jobID": "platforms", "matrix": None}],
                     "status": "completed", "conclusion": "skipped", "sections": []})
        fixture.write_text(json.dumps(self.raw))
        outcome = run_checked_command(self.workspace, command, config,
                                      head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(outcome.returncode, 0, outcome.error)
        for change in ({"conclusion": "success"}, {"status": "queued"}):
            refused = copy.deepcopy(self.raw)
            refused["tree"]["groups"][0]["jobs"][-1].update(change)
            fixture.write_text(json.dumps(refused))
            result = run_checked_command(self.workspace, command, config,
                                         head=self.expected.sha, tree=self.expected.git_tree)
            self.assertEqual(result.returncode, 1)
        source.write_text(source.read_text().replace("needs.plan.outputs.lanes != '[]'", "false"))
        fixture.write_text(json.dumps(self.raw))
        result = run_checked_command(self.workspace, command, config,
                                     head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(result.returncode, 1, "constant false cannot erase dynamic platform coverage")

    @requires_yaml_tooling
    def test_runtime_proves_planner_before_resolving_all_matrix_checks(self) -> None:
        workflows = self.workspace / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text(
            "jobs:\n  plan:\n    outputs: {matrix: '${{ steps.plan.outputs.matrix }}'}\n"
            "    steps: [{name: Plan, run: plan}]\n"
            "  tests:\n    needs: plan\n    strategy:\n"
            "      matrix: '${{ fromJSON(needs.plan.outputs.matrix) }}'\n"
            "    steps:\n      - {name: Run tests, run: tests}\n"
            "      - name: Integration\n        run: integration\n"
            "        if: contains(fromJSON(needs.plan.outputs.suites), 'integration')\n")
        config = replace(self.config(), qualified=True, report_source="stdout", jobs=(
            DeclaredReplayJob("ci.yml:plan", self.expected.required_jobs[0], ("tests",), True),
            DeclaredReplayJob("ci.yml:tests", self.expected.required_jobs[0], ("tests",), True)))
        original = self.raw["tree"]["groups"][0]["jobs"][0]
        jobs = []
        for identity, matrix, name in (("plan", None, "Plan"), ("tests", {"lane": "left"}, "Run tests"),
                                       ("tests", {"lane": "right"}, "Run tests")):
            job = copy.deepcopy(original)
            job.update(key=identity+str(matrix), job_id=identity, matrix=matrix,
                       identity=[{"jobID": identity, "matrix": matrix}])
            job["sections"][0]["name"] = name
            if identity == "tests":
                job["sections"].append({"name": "Integration", "stage": "Main", "status": "completed",
                                        "conclusion": "skipped", "first_seq": None, "last_seq": None})
            jobs.append(job)
        jobs[0]["output_evidence"] = {"schema_version": 1, "seq": 21,
                                       "values": {"matrix": '{"lane":["left","right"]}',
                                                  "suites": '["unit"]'}, "error": None}
        self.raw["tree"]["groups"][0]["jobs"] = jobs
        fixture = self.workspace / "matrix.json"
        command = self.command(f"print(pathlib.Path({str(fixture)!r}).read_text())\n")
        fixture.write_text(json.dumps(self.raw))
        outcome = run_checked_command(self.workspace, command, config,
                                      head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(outcome.returncode, 0, outcome.error)
        skipped = copy.deepcopy(jobs[1]["sections"][-1])
        for change in ({"conclusion": "failure"}, {"conclusion": "success"},
                       {"stage": "Post"}, {"status": "queued"}):
            jobs[1]["sections"][-1].update(change)
            fixture.write_text(json.dumps(self.raw))
            rejected = run_checked_command(self.workspace, command, config,
                                           head=self.expected.sha, tree=self.expected.git_tree)
            self.assertEqual(rejected.returncode, 1)
            jobs[1]["sections"][-1] = copy.deepcopy(skipped)
        jobs[1]["sections"].pop()
        fixture.write_text(json.dumps(self.raw))
        rejected = run_checked_command(self.workspace, command, config,
                                       head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(rejected.returncode, 1)
        jobs[1]["sections"].append(skipped)
        for index in (0, 1, 2):
            jobs[index]["sections"][0]["conclusion"] = "skipped"
            fixture.write_text(json.dumps(self.raw))
            rejected = run_checked_command(self.workspace, command, config,
                                           head=self.expected.sha, tree=self.expected.git_tree)
            self.assertEqual(rejected.returncode, 1)
            jobs[index]["sections"][0]["conclusion"] = "success"
        jobs.pop()
        fixture.write_text(json.dumps(self.raw))
        rejected = run_checked_command(self.workspace, command, config,
                                       head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(rejected.returncode, 1)
        source = workflows / "ci.yml"
        source.write_text(source.read_text().replace("name: Plan", "id: plan"))
        marker = self.workspace / "unexpected-execution"
        command = self.command(f"pathlib.Path({str(marker)!r}).write_text('ran')\nprint(pathlib.Path({str(fixture)!r}).read_text())\n")
        rejected = run_checked_command(self.workspace, command, config,
                                       head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(rejected.returncode, 1)
        self.assertFalse(marker.exists(), "invalid producer definition must refuse before execution")

    @requires_yaml_tooling
    def test_source_derived_runtime_requires_each_bound_profile(self) -> None:
        workflows = self.workspace / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text(
            "jobs:\n  lint:\n    uses: ./.github/workflows/check.yml\n"
            "    with: {compile: false, clippy: true}\n"
            "  build:\n    uses: ./.github/workflows/check.yml\n"
            "    with: {compile: true, clippy: false}\n")
        (workflows / "check.yml").write_text(
            "on:\n  workflow_call:\n    inputs:\n"
            "      compile: {type: boolean}\n      clippy: {type: boolean}\n"
            "jobs:\n  tests:\n    steps:\n"
            "      - {name: Run tests, run: tests, if: inputs.compile}\n"
            "      - {name: Clippy, run: lint, if: inputs.clippy}\n"
            "      - {name: Failure log, run: diagnostic, if: 'failure()'}\n")
        config = replace(self.config(), qualified=True, report_source="stdout", jobs=(
            replace(self.config().jobs[0], source_job="check.yml:tests", derive_checks=True),))
        original = self.raw["tree"]["groups"][0]["jobs"][0]
        jobs = []
        for caller in ("lint", "build"):
            job = copy.deepcopy(original)
            job.update(job_id="tests", matrix=None, identity=[
                {"jobID": caller, "matrix": None}, {"jobID": "tests", "matrix": None}])
            base = copy.deepcopy(original["sections"][0])
            job["sections"] = []
            for name, executed in (("Run tests", caller == "build"), ("Clippy", caller == "lint"), ("Failure log", False)):
                job["sections"].append({**base, "name": name, "stage": "Main", "status": "completed",
                                        "conclusion": "success" if executed else "skipped",
                                        "first_seq": 1 if executed else None, "last_seq": 2 if executed else None})
            jobs.append(job)
        self.raw["tree"]["groups"][0]["jobs"] = jobs
        fixture = self.workspace / "profiles.json"
        command = self.command(f"print(pathlib.Path({str(fixture)!r}).read_text())\n")
        for conclusion, valid in (("success", True), ("skipped", False), ("failure", False)):
            jobs[0]["sections"][1]["conclusion"] = conclusion
            fixture.write_text(json.dumps(self.raw))
            outcome = run_checked_command(self.workspace, command, config,
                                          head=self.expected.sha, tree=self.expected.git_tree)
            self.assertEqual(outcome.returncode == 0, valid, outcome.error)
        jobs[0]["sections"].pop(1)
        fixture.write_text(json.dumps(self.raw))
        outcome = run_checked_command(self.workspace, command, config,
                                      head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(outcome.returncode, 1)

    @requires_yaml_tooling
    def test_qualified_runtime_derives_all_caller_and_leaf_legs(self) -> None:
        workflows = self.workspace / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text(
            "jobs:\n  first:\n    name: Same\n    uses: ./.github/workflows/check.yml\n"
            "    strategy: {matrix: {target: [linux, windows]}}\n"
            "  second:\n    name: Same\n    uses: ./.github/workflows/check.yml\n")
        (workflows / "check.yml").write_text(
            "on: {workflow_call: {}}\njobs:\n  tests:\n    name: Same\n"
            "    strategy: {matrix: {shard: [1, 2]}}\n    steps:\n"
            "      - {name: Run tests, run: true}\n")
        config = replace(self.config(), qualified=True, report_source="stdout", jobs=(
            replace(self.config().jobs[0], source_job="check.yml:tests"),))
        original = self.raw["tree"]["groups"][0]["jobs"][0]
        jobs = []
        for caller, target in (("first", "linux"), ("first", "windows"), ("second", None)):
            for shard in (1, 2):
                job = copy.deepcopy(original)
                job.update(key="Same", job_id="tests", matrix={"shard": shard}, identity=[
                    {"jobID": caller, "matrix": {"target": target} if target else None},
                    {"jobID": "tests", "matrix": {"shard": shard}},
                ])
                jobs.append(job)
        self.raw["tree"]["groups"][0]["jobs"] = jobs
        fixture = self.workspace / "qualified.json"
        fixture.write_text(json.dumps(self.raw))
        command = self.command(f"print(pathlib.Path({str(fixture)!r}).read_text())\n")
        outcome = run_checked_command(self.workspace, command, config,
                                      head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(outcome.returncode, 0, outcome.error)
        jobs.pop()
        fixture.write_text(json.dumps(self.raw))
        rejected = run_checked_command(self.workspace, command, config,
                                       head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("required job is missing", rejected.error or "")
        (workflows / "ci.yml").write_text("jobs:\n  dynamic:\n    strategy: {matrix: '${{ needs.plan.outputs.matrix }}'}\n")
        marker = self.workspace / "started"
        command = self.command(f"pathlib.Path({str(marker)!r}).touch()\n")
        rejected = run_checked_command(self.workspace, command, config,
                                       head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(rejected.returncode, 1)
        self.assertFalse(marker.exists(), "unproved graph must reject before scheduling")

    def command(self, body: str) -> tuple[str, ...]:
        script = self.workspace / "runner.py"
        script.write_text("import os, pathlib\n" + body, encoding="utf-8")
        return (sys.executable, str(script))

    def config(self) -> ReplayConfig:
        return ReplayConfig("owner/repo", ".github/workflows/ci.yml", "minimal",
                            (DeclaredReplayJob("ci.yml:tests", self.expected.required_jobs[0], ("tests",)),))

    def test_zero_exit_without_report_cannot_prove_a_gate(self) -> None:
        outcome = run_checked_command(self.workspace, self.command("pass\n"), self.config(),
                                      head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(outcome.returncode, 1)
        self.assertIn("proof rejected", outcome.error or "")

    def test_direct_runner_stdout_uses_the_same_verifier(self) -> None:
        fixture = self.workspace / "fixture.json"
        fixture.write_text(json.dumps(self.raw), encoding="utf-8")
        command = self.command(f"print(pathlib.Path({str(fixture)!r}).read_text())\n")
        config = replace(self.config(), report_source="stdout")
        outcome = run_checked_command(self.workspace, command, config,
                                      head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(outcome.returncode, 0, outcome.error)
        self.raw["conclusion"] = "incomplete"
        fixture.write_text(json.dumps(self.raw), encoding="utf-8")
        rejected = run_checked_command(self.workspace, command, config,
                                       head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("conclusion", rejected.error or "")

    def test_successful_child_report_is_validated(self) -> None:
        fixture = self.workspace / "fixture.json"
        fixture.write_text(json.dumps(self.raw), encoding="utf-8")
        command = self.command(
            "pathlib.Path(os.environ['CI_LINT_GATE_REPLAY_REPORT']).write_text("
            f"pathlib.Path({str(fixture)!r}).read_text())\n")
        outcome = run_checked_command(self.workspace, command, self.config(),
                                      head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(outcome.returncode, 0, outcome.error)
        self.raw["dirty"] = "d" * 64
        fixture.write_text(json.dumps(self.raw), encoding="utf-8")
        rejected = run_checked_command(self.workspace, command, self.config(),
                                       head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("dirty", rejected.error or "")

    def test_alternate_whole_workflow_requires_exact_report_selection(self) -> None:
        config = replace(self.config(), selections=(
            ReplaySelection("tests", None, "pull_request", (), ".github/workflows/dylint.yml"),))
        fixture = self.workspace / "alternate.json"
        command = self.command(
            "pathlib.Path(os.environ['CI_LINT_GATE_REPLAY_REPORT']).write_text("
            f"pathlib.Path({str(fixture)!r}).read_text())\n")
        for workflow, job, accepted in (
            (".github/workflows/dylint.yml", None, True),
            (".github/workflows/ci.yml", None, False),
            (".github/workflows/dylint.yml", "tests", False),
        ):
            with self.subTest(workflow=workflow, job=job):
                fixture.write_text(json.dumps({**self.raw, "workflow": workflow, "job": job}))
                outcome = run_checked_command(self.workspace, command, config,
                                              head=self.expected.sha, tree=self.expected.git_tree, lane="tests")
                self.assertEqual(outcome.returncode == 0, accepted, outcome.error)

    def test_report_symlink_and_duplicate_keys_are_rejected(self) -> None:
        fixture = self.workspace / "report.json"
        fixture.write_text('{"sha":"a","sha":"b"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            _read_report(fixture)
        link = self.workspace / "report-link.json"
        link.symlink_to(fixture)
        with self.assertRaises(ValueError):
            _read_report(link)

    def test_failed_child_never_accepts_report(self) -> None:
        outcome = run_checked_command(self.workspace, self.command("raise SystemExit(4)\n"),
                                      self.config(), head=self.expected.sha, tree=self.expected.git_tree)
        self.assertEqual(outcome.returncode, 4)
