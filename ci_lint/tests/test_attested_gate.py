"""A real Git attestation must reach the shared required-check aggregator."""

import copy
import unittest
from unittest.mock import patch
from ci_lint.tests import test_gate_trust as fixtures
from ci_lint.tests.helpers import requires_yaml_tooling
from ci_lint.attestations import make
from ci_lint.gate_trust import TrustInput, decide
from ci_lint.local_gate_cli import _job_decisions
from ci_lint.runtime.gate import compute_gate
from ci_lint.hosted_attestations import verified_jobs


@requires_yaml_tooling
class AttestedGateTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TrustCase()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.repo = self.fixture.repo
        definition = "version: 1\ngates:\n  rust/all/lint: {lane: lint}\njobs:\n  ci.yml:lint: [rust/all/lint]\n"
        self.base = self.fixture.commit("definition", {"ci-attestations.yml": definition}, lanes=None)
        self.fixture.commit("feature", {"src.txt": "two\n"})
        tree = fixtures._git(self.repo, "rev-parse", "HEAD^{tree}")
        trailer = make("rust/all/lint", tree=tree, parents=(self.base,), lane="lint", key="k",
                       via="run", secs=1).trailer()
        message = fixtures._git(self.repo, "log", "-1", "--format=%B")
        fixtures._git(self.repo, "commit", "-q", "--amend", "-m", message + "\n" + trailer)
        self.head = fixtures._git(self.repo, "rev-parse", "HEAD")

    def test_verified_local_attestation_reaches_ci_ok(self):
        inp = TrustInput("pull_request", self.head, self.base, "OWNER", "o/r", "o/r", ())
        decision = decide(self.repo, inp)
        self.assertTrue(decision.trusted, decision.detail)
        jobs = _job_decisions(self.repo, inp, decision.trusted)
        self.assertTrue(next(item.skip for item in jobs if item.job == "ci.yml:lint"))
        needs = self.needs()
        jobs = verified_jobs(self.repo, "ci.yml", self.payload(), event="pull_request", needs=needs)
        report = compute_gate({"required_jobs": ["lint"]}, needs, attested_jobs=jobs)
        self.assertTrue(report.ok, report.findings)

    def payload(self):
        return {"pull_request": {"head": {"sha": self.head, "repo": {"full_name": "o/r"}},
                                 "base": {"sha": self.base, "repo": {"full_name": "o/r"}},
                                 "author_association": "OWNER", "labels": []}}

    def needs(self):
        return {"verify": {"result": "success", "outputs": {"skip_lint": "true"}},
                "lint": {"result": "skipped"}}

    def test_verifier_outputs_cannot_replace_the_original_proof(self):
        for alteration in (
            'payload["pull_request"]["author_association"] = "CONTRIBUTOR"',
            'payload["pull_request"]["head"]["repo"]["full_name"] = "fork/repo"',
            'payload["pull_request"]["base"]["sha"] = "a" * 40',
            'payload["pull_request"]["labels"] = [{"name": "ci-full"}]',
            'needs["verify"]["result"] = "failure"',
            'needs.pop("verify")',
            'needs["verify"].pop("outputs")',
            'needs["verify"]["outputs"]["skip_lint"] = True',
            'needs["verify"]["outputs"]["skip_lint"] = "false"',
        ):
            with self.subTest(alteration=alteration):
                payload, needs = copy.deepcopy(self.payload()), copy.deepcopy(self.needs())
                exec(alteration)
                jobs = verified_jobs(self.repo, "ci.yml", payload, event="pull_request", needs=needs)
                report = compute_gate({"required_jobs": ["lint"]}, needs, attested_jobs=jobs)
                self.assertFalse(report.ok)

    def test_push_dispatch_local_replay_and_other_workflow_never_credit_a_skip(self):
        for workflow, event, local in (("ci.yml", "push", False), ("ci.yml", "workflow_dispatch", False),
                                       ("ci.yml", "pull_request", True), ("other.yml", "pull_request", False),
                                       ("../ci.yml", "pull_request", False)):
            with self.subTest(workflow=workflow, event=event, local=local):
                jobs = verified_jobs(self.repo, workflow, self.payload(), event=event,
                                     needs=self.needs(), local_replay=local)
                self.assertEqual(jobs, ())

    def test_a_failed_missing_or_running_job_cannot_be_overridden(self):
        jobs = verified_jobs(self.repo, "ci.yml", self.payload(), event="pull_request", needs=self.needs())
        self.assertTrue(jobs)
        for needs in ({"lint": {"result": "failure"}}, {"lint": {"result": "running"}}, {}):
            with self.subTest(needs=needs):
                self.assertFalse(compute_gate({"required_jobs": ["lint"]}, needs, attested_jobs=jobs).ok)
        self.assertFalse(compute_gate({"required_jobs": ["lint"], "mergeable": False},
                                      self.needs(), attested_jobs=jobs).ok)

    def test_original_attestation_expiry_is_rechecked_by_the_aggregator(self):
        with patch("ci_lint.attestations.time.time", return_value=10**12):
            jobs = verified_jobs(self.repo, "ci.yml", self.payload(), event="pull_request", needs=self.needs())
        self.assertFalse(compute_gate({"required_jobs": ["lint"]}, self.needs(), attested_jobs=jobs).ok)

    def test_actual_cli_reverifies_before_accepting_the_required_skip(self):
        import contextlib
        import io
        import json
        import os
        from ci_lint.cli import main
        for name, raw in (("plan", {"required_jobs": ["lint"]}), ("needs", self.needs()),
                          ("event", self.payload())):
            (self.repo / (name + ".json")).write_text(json.dumps(raw))
        argv = ["gate", "--repo", str(self.repo), "--plan", str(self.repo / "plan.json"),
                "--needs", str(self.repo / "needs.json"), "--event", str(self.repo / "event.json"),
                "--attested-workflow", "ci.yml", "--json"]
        for event, expected in (("pull_request", 0), ("push", 1)):
            with patch.dict(os.environ, {"GITHUB_EVENT_NAME": event, "ACT": "false"}):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = main(argv)
                self.assertEqual(code, expected, output.getvalue())
                self.assertEqual(json.loads(output.getvalue())["statuses"][0]["locally_attested"], code == 0)
