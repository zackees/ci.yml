"""Reusable selections include every called job and caller prerequisite."""

import unittest
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ci_lint.finding import Status
from ci_lint.tests.helpers import requires_yaml_tooling
from ci_lint.workflow_replay import ReplayJob
from ci_lint.workflow_replay_config import DeclaredReplayJob, ReplayConfig, ReplaySelection
from ci_lint.workflow_replay_dependencies import check_selection_dependencies
from ci_lint.workflow_replay_expansion import expand_selection
from ci_lint.workflow_replay_static import check_replay_static
from ci_lint.workflow_scan import ParsedYamlFile
from ci_lint.yaml_io import LoadStatus


def workflow(path, document):
    return ParsedYamlFile(path, document, LoadStatus.OK, None)


class ReplayExpansionTest(unittest.TestCase):
    def setUp(self):
        self.entry = workflow(".github/workflows/ci.yml", {
            "name": "CI", "jobs": {
                "verify": {"name": "Verify", "steps": []},
                "linux": {"needs": "verify", "uses": "./.github/workflows/check.yml"},
            },
        })
        self.called = workflow(".github/workflows/check.yml", {
            "name": "Check", "on": {"workflow_call": {}}, "jobs": {
                "check": {"name": "Workspace", "steps": []},
                "facade": {"name": "Python facade", "needs": "check", "steps": []},
            },
        })

    def test_all_callee_jobs_and_caller_dependencies(self):
        proof = expand_selection((self.entry, self.called), self.entry.path, "linux")
        self.assertIsNone(proof.problem)
        self.assertEqual({job.source_job for job in proof.jobs},
                         {"ci.yml:verify", "check.yml:check", "check.yml:facade"})
        self.assertEqual({job.key for job in proof.jobs},
                         {"CI/Verify", "linux/Check/Workspace", "linux/Check/Python facade"})

    def test_unresolvable_calls_are_unproven(self):
        for uses in ("owner/repo/.github/workflows/check.yml@main", "${{ inputs.workflow }}",
                     "./.github/workflows/missing.yml", "./.github/workflows/../check.yml"):
            entry = replace(self.entry, document={"jobs": {"linux": {"uses": uses}}})
            with self.subTest(uses=uses):
                self.assertIsNotNone(expand_selection((entry, self.called), entry.path, "linux").problem)

    def test_cycles_missing_jobs_and_matrix_are_unproven(self):
        for job in ({"needs": "absent"}, {"needs": "linux"}, {"strategy": {"matrix": {"os": ["linux"]}}}):
            entry = replace(self.entry, document={"jobs": {"linux": job}})
            with self.subTest(job=job):
                self.assertIsNotNone(expand_selection((entry,), entry.path, "linux").problem)

    def test_missing_workflow_call_and_unparsed_document_are_unproven(self):
        for called in (replace(self.called, document={"jobs": {"check": {}}}),
                       replace(self.called, document=None),
                       replace(self.called, document={"on": {"workflow_call": {}}, "jobs": {}})):
            with self.subTest(called=called):
                self.assertIsNotNone(expand_selection((self.entry, called), self.entry.path, "linux").problem)

    def test_repeated_callee_identity_is_ambiguous(self):
        entry = replace(self.entry, document={"jobs": {
            "first": {"uses": "./.github/workflows/check.yml"},
            "second": {"needs": "first", "uses": "./.github/workflows/check.yml"},
        }})
        proof = expand_selection((entry, self.called), entry.path, "second")
        self.assertIsNotNone(proof.problem)

    def test_workflow_recursion_is_unproven(self):
        called = replace(self.called, document={"on": {"workflow_call": {}}, "jobs": {
            "recursive": {"uses": "./.github/workflows/check.yml"},
        }})
        self.assertIsNotNone(expand_selection((self.entry, called), self.entry.path, "linux").problem)

    def config(self):
        return ReplayConfig("owner/repo", self.entry.path, "minimal", (
            DeclaredReplayJob("ci.yml:verify", ReplayJob("CI/Verify", ("Tests",)), ("linux",)),
            DeclaredReplayJob("check.yml:check", ReplayJob("linux/Check/Workspace", ("Tests",)), ("linux",)),
            DeclaredReplayJob("check.yml:facade", ReplayJob("linux/Check/Python facade", ("Tests",)), ("linux",)),
        ), (ReplaySelection("linux", "linux", "workflow_dispatch", ()),))

    def test_declared_pair_cannot_omit_caller_verifier_or_facade(self):
        config = self.config()
        for removed in ("ci.yml:verify", "check.yml:facade"):
            partial = replace(config, jobs=tuple(job for job in config.jobs if job.source_job != removed))
            findings = check_selection_dependencies(partial, self.entry.document, (self.entry, self.called))
            self.assertEqual(findings[0].status, Status.VIOLATION)
            self.assertIn(removed, findings[0].message)

    def static_files(self):
        files = []
        for item in (self.entry, self.called):
            document = {**item.document, "jobs": {
                name: job if "uses" in job else {**job, "steps": [{"name": "Tests", "run": "pytest"}]}
                for name, job in item.document["jobs"].items()
            }}
            files.append(replace(item, document=document))
        return files

    def test_static_reusable_identity_and_steps_are_bound(self):
        config = self.config()
        with patch("ci_lint.workflow_replay_static.load_workflows", return_value=self.static_files()):
            self.assertEqual(check_replay_static(config, Path("/repo")), [])
            wrong = replace(config.jobs[1], proof=ReplayJob("Check/Workspace", ("Tests",)))
            changed = replace(config, jobs=(config.jobs[0], wrong, config.jobs[2]))
            findings = check_replay_static(changed, Path("/repo"))
            self.assertTrue(any("execution key" in item.message for item in findings))

    def test_a_leaf_declaration_cannot_waive_an_executable_step(self):
        files = self.static_files()
        files[1].document["jobs"]["facade"]["steps"].append({"name": "Checkout", "uses": "actions/checkout@v4"})
        with patch("ci_lint.workflow_replay_static.load_workflows", return_value=files):
            findings = check_replay_static(self.config(), Path("/repo"))
        self.assertTrue(any("omitted=['Checkout']" in item.message for item in findings))

    @requires_yaml_tooling
    def test_real_workflow_files_resolve_the_reusable_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            workflows = repo / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "ci.yml").write_text(
                "name: CI\non: {workflow_dispatch: {}}\njobs:\n"
                "  verify:\n    name: Verify\n    steps:\n"
                "      - {name: Tests, run: pytest}\n"
                "  linux:\n    needs: verify\n    uses: ./.github/workflows/check.yml\n"
            )
            (workflows / "check.yml").write_text(
                "name: Check\non: {workflow_call: {}}\njobs:\n"
                "  check:\n    name: Workspace\n    steps:\n"
                "      - {name: Tests, run: pytest}\n"
                "  facade:\n    name: Python facade\n    needs: check\n    steps:\n"
                "      - {name: Tests, run: pytest}\n"
            )
            self.assertEqual(check_replay_static(self.config(), repo), [])
