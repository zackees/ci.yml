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
            'payload["pull_request"]["title"] = "[ci-full] add native coverage"',
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

    def test_pr_head_cannot_weaken_base_attestation_requirement(self):
        import contextlib
        import io
        import json
        import os
        from ci_lint.cli import main
        text = (self.repo / "local-gate.toml").read_text()
        # The trusted base requires a local gate, while the unproved head
        # changes only the head's requirement to shadow.
        base_text = text.replace("[gate]\n", '[gate]\nmode = "enforce"\n', 1)
        base = self.fixture.commit("require attestation", {"local-gate.toml": base_text}, lanes=None)
        for head_text in (
            base_text.replace('mode = "enforce"', 'mode = "shadow"', 1),
            base_text.replace("[gate]\n", '[gate]\nexempt-authors = ["developer"]\n', 1),
        ):
            with self.subTest(head_policy=head_text):
                head = self.fixture.commit("weaken requirement", {"local-gate.toml": head_text}, lanes=None)
                payload = self.payload()
                payload["pull_request"]["head"]["sha"] = head
                payload["pull_request"]["base"]["sha"] = base
                event_path = self.repo / ".git" / "test-event.json"
                event_path.write_text(json.dumps(payload))
                with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request",
                                            "GITHUB_EVENT_PATH": str(event_path), "ACT": "false"}):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        code = main(["local-gate", "verify", "--repo", str(self.repo), "--trust",
                                     "--author", "developer"])
                self.assertEqual(code, 1, output.getvalue())
                self.assertNotIn("mode = shadow", output.getvalue())

    def test_missing_or_unavailable_pr_base_cannot_use_head_shadow_policy(self):
        import contextlib
        import io
        import json
        import os
        from ci_lint.cli import main
        text = (self.repo / "local-gate.toml").read_text().replace(
            "[gate]\n", '[gate]\nmode = "shadow"\n', 1)
        head = self.fixture.commit("unproved shadow", {"local-gate.toml": text}, lanes=None)
        for base in (None, "a" * 40):
            with self.subTest(base=base):
                payload = self.payload()
                payload["pull_request"]["head"]["sha"] = head
                payload["pull_request"]["base"]["sha"] = base
                event_path = self.repo / ".git" / "test-event.json"
                event_path.write_text(json.dumps(payload))
                with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request",
                                            "GITHUB_EVENT_PATH": str(event_path), "ACT": "false"}):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = main(["local-gate", "verify", "--repo", str(self.repo), "--trust"])
                self.assertEqual(code, 2)

    def test_readable_base_without_declaration_preserves_initial_enrollment(self):
        import contextlib
        import io
        import json
        import os
        from ci_lint.cli import main
        text = (self.repo / "local-gate.toml").read_text().replace(
            "[gate]\n", '[gate]\nmode = "shadow"\n', 1)
        fixtures._git(self.repo, "rm", "-q", "local-gate.toml")
        base = self.fixture.commit("before enrollment", {}, lanes=None)
        head = self.fixture.commit("initial enrollment", {"local-gate.toml": text}, lanes=None)
        payload = self.payload()
        payload["pull_request"]["head"]["sha"] = head
        payload["pull_request"]["base"]["sha"] = base
        event_path = self.repo / ".git" / "test-event.json"
        event_path.write_text(json.dumps(payload))
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request",
                                    "GITHUB_EVENT_PATH": str(event_path), "ACT": "false"}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["local-gate", "verify", "--repo", str(self.repo), "--trust"])
        self.assertEqual(code, 0, output.getvalue())
        self.assertIn("not-opted-in", output.getvalue())

    def test_unreadable_base_tree_or_declared_blob_cannot_bootstrap(self):
        import contextlib
        import io
        import json
        import os
        from ci_lint.cli import main
        from ci_lint.local_gate import _git
        text = (self.repo / "local-gate.toml").read_text().replace(
            "[gate]\n", '[gate]\nmode = "shadow"\n', 1)
        head = self.fixture.commit("unproved shadow", {"local-gate.toml": text}, lanes=None)
        missing = "a" * 40
        blob_tree = _git(self.repo, "mktree", "--missing",
                         stdin=f"100644 blob {missing}\tlocal-gate.toml\n").strip()
        for tree in (missing, blob_tree):
            with self.subTest(tree=tree):
                base = _git(self.repo, "hash-object", "-w", "-t", "commit", "--stdin",
                            stdin=f"tree {tree}\nauthor Test <test@example.com> 1 +0000\n"
                                  "committer Test <test@example.com> 1 +0000\n\nunreadable policy\n").strip()
                _git(self.repo, "cat-file", "-e", f"{base}^{{commit}}")
                payload = self.payload()
                payload["pull_request"]["head"]["sha"] = head
                payload["pull_request"]["base"]["sha"] = base
                event_path = self.repo / ".git" / "test-event.json"
                event_path.write_text(json.dumps(payload))
                with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request",
                                            "GITHUB_EVENT_PATH": str(event_path), "ACT": "false"}):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = main(["local-gate", "verify", "--repo", str(self.repo), "--trust"])
                self.assertEqual(code, 2)

    def test_malformed_base_policy_cannot_bootstrap_shadow_head(self):
        import contextlib
        import io
        import json
        import os
        from ci_lint.cli import main
        text = (self.repo / "local-gate.toml").read_text().replace(
            "[gate]\n", '[gate]\nmode = "shadow"\n', 1)
        fixtures._git(self.repo, "rm", "-q", "local-gate.toml")
        base = self.fixture.commit("malformed base policy", {
            "ci.toml": '[local.gate]\nmode = "enforce"\ninvalid = [\n',
        }, lanes=None)
        head = self.fixture.commit("unproved shadow", {
            "ci.toml": "", "local-gate.toml": text,
        }, lanes=None)
        payload = self.payload()
        payload["pull_request"]["head"]["sha"] = head
        payload["pull_request"]["base"]["sha"] = base
        event_path = self.repo / ".git" / "test-event.json"
        event_path.write_text(json.dumps(payload))
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request",
                                    "GITHUB_EVENT_PATH": str(event_path), "ACT": "false"}):
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["local-gate", "verify", "--repo", str(self.repo), "--trust"])
        self.assertEqual(code, 2)
