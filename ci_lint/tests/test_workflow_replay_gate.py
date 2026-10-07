"""Replay proof rejection must precede stamping or recording a lane pass."""

from __future__ import annotations

import sys
import json
import copy
import unittest
from dataclasses import replace

from ci_lint.lane_cache import cache_dir
from ci_lint.local_gate import run_gate
from ci_lint.tests.test_lane_cache import LanedRepo, _git
from ci_lint.tests.helpers import requires_yaml_tooling
from ci_lint.tests import test_lane_cache
from ci_lint.tests.test_workflow_replay import WorkflowReplayTest

REPLAY = '''
[gate.replay]
repository = "owner/repo"
workflow = "ci.yml"
mode = "minimal"
[[gate.replay.jobs]]
source-job = "ci.yml:lint"
key = "CI/Lint"
steps = ["Check"]
lanes = ["lint"]
'''


@requires_yaml_tooling
class ReplayGateIntegrationTest(LanedRepo):
    def setUp(self) -> None:
        super().setUp()
        path = self.repo / "local-gate.toml"
        path.write_text(path.read_text() + REPLAY, encoding="utf-8")
        self.write(".github/workflows/ci.yml", "name: CI\njobs:\n  lint:\n    name: Lint\n    steps:\n      - name: Check\n        run: echo lint\n")
        self.commit("declare replay")

    def test_lane_zero_exit_without_report_is_not_recorded_or_stamped(self) -> None:
        outcome = run_gate(self.repo, self.config())
        self.assertNotEqual(outcome.exit_code, 0)
        self.assertEqual(list((cache_dir(self.repo) / "lint").glob("*.json")), [])
        self.assertNotIn("Local-Gate:", _git(self.repo, "log", "-1", "--format=%B"))

    def test_opaque_zero_exit_without_report_is_not_stamped(self) -> None:
        self.write("success.py", "raise SystemExit(0)\n")
        self.commit("successful unproved command")
        config = replace(self.config(), run=(sys.executable, "success.py"), lanes=())
        outcome = run_gate(self.repo, config)
        self.assertNotEqual(outcome.exit_code, 0)
        self.assertNotIn("Local-Gate:", _git(self.repo, "log", "-1", "--format=%B"))

    def test_static_omission_is_rejected_before_command_or_cache(self) -> None:
        self.write(".github/workflows/ci.yml", "name: CI\njobs:\n  lint:\n    name: Lint\n    steps:\n      - name: Check\n        run: echo lint\n      - name: Other check\n        run: echo other\n")
        self.commit("uncovered remote step")
        outcome = run_gate(self.repo, self.config())
        self.assertNotEqual(outcome.exit_code, 0)
        self.assertIn("omitted=['Other check']", outcome.message)
        self.assertEqual(list((cache_dir(self.repo) / "lint").glob("*.json")), [])

    def test_invalid_replay_declaration_does_not_fall_back_to_plain_gate(self) -> None:
        path = self.repo / "local-gate.toml"
        path.write_text(path.read_text().replace('steps = ["Check"]', 'steps = []'))
        from ci_lint.local_gate import load_gate_config  # noqa: PLC0415
        loaded = load_gate_config(self.repo)
        self.assertIsNone(loaded.config)
        self.assertTrue(loaded.findings)

    def test_unmapped_replay_job_cannot_bypass_lane_proof(self) -> None:
        path = self.repo / "local-gate.toml"
        path.write_text(path.read_text().replace('lanes = ["lint"]', 'lanes = []'))
        from ci_lint.local_gate import load_gate_config  # noqa: PLC0415
        loaded = load_gate_config(self.repo)
        self.assertIsNone(loaded.config)
        self.assertTrue(loaded.findings)

    def test_wrong_replay_table_type_cannot_disable_proof(self) -> None:
        path = self.repo / "local-gate.toml"
        text = path.read_text().replace(REPLAY, "").replace("[gate]\n", "[gate]\nreplay = false\n", 1)
        path.write_text(text)
        from ci_lint.local_gate import load_gate_config  # noqa: PLC0415
        loaded = load_gate_config(self.repo)
        self.assertIsNone(loaded.config)
        self.assertTrue(loaded.findings)



@requires_yaml_tooling
class ReplayFullRunIntegrationTest(test_lane_cache.FullRunReceiptTest):
    def test_lane_receipt_without_runner_report_caches_nothing(self) -> None:
        path = self.repo / "local-gate.toml"
        path.write_text(path.read_text() + REPLAY, encoding="utf-8")
        self.write(".github/workflows/ci.yml", "name: CI\njobs:\n  lint:\n    name: Lint\n    steps:\n      - name: Check\n        run: echo lint\n")
        self.commit("full command requires runner evidence")
        outcome = run_gate(self.repo, self.config())
        self.assertNotEqual(outcome.exit_code, 0)
        for lane in ("lint", "tests"):
            self.assertEqual(list((cache_dir(self.repo) / lane).glob("*.json")), [])
        self.assertNotIn("Local-Gate:", _git(self.repo, "log", "-1", "--format=%B"))


@requires_yaml_tooling
class QualifiedFullRunTest(LanedRepo):
    """Synthetic runner receipts exercise real gate/cache/stamp integration."""

    def setUp(self):
        super().setUp()
        path = self.repo / "local-gate.toml"
        text = path.read_text().replace('"gate.py"]', '"full.py"]', 1)
        text += '''
[gate.full-run]
receipt = "lane-passes-v1"
min-misses = 2
[gate.replay]
repository = "owner/repo"
workflow = "ci.yml"
mode = "minimal"
qualified = true
report-source = "stdout"
[[gate.replay.jobs]]
source-job = "ci.yml:prepare"
lanes = ["lint", "tests"]
[[gate.replay.jobs]]
source-job = "ci.yml:lint"
lanes = ["lint"]
[[gate.replay.jobs]]
source-job = "ci.yml:tests"
lanes = ["tests"]
[[gate.replay.selections]]
lane = "lint"
job = "lint"
event = "pull_request"
[[gate.replay.selections]]
lane = "tests"
job = "tests"
event = "pull_request"
'''
        path.write_text(text)
        self.write(".github/workflows/ci.yml", '''jobs:
  prepare:
    steps: [{name: Prepare, run: prepare}]
  lint:
    needs: prepare
    steps: [{name: Lint, run: lint}]
  tests:
    needs: prepare
    steps: [{name: Tests, run: tests}]
''')
        fixture = WorkflowReplayTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        raw = copy.deepcopy(fixture.raw)
        original = raw["tree"]["groups"][0]["jobs"][0]
        jobs = []
        for job_id, name in (("prepare", "Prepare"), ("lint", "Lint"), ("tests", "Tests")):
            job = copy.deepcopy(original)
            job.update(key=job_id, job_id=job_id, matrix=None, identity=[{"jobID": job_id, "matrix": None}])
            job["sections"][0]["name"] = name
            jobs.append(job)
        raw["tree"]["groups"][0]["jobs"] = jobs
        self.write("receipt-template.json", json.dumps(raw))
        self.write("full.py", f'''import json, os, pathlib
log = pathlib.Path({str(self.log)!r})
log.write_text(log.read_text() + "full\\n" if log.exists() else "full\\n")
raw = json.loads(pathlib.Path("receipt-template.json").read_text())
raw.update(workspace=str(pathlib.Path.cwd()), sha=os.environ["CI_LINT_GATE_HEAD"], git_tree=os.environ["CI_LINT_GATE_TREE"])
if pathlib.Path("missing-tests").exists():
    raw["tree"]["groups"][0]["jobs"].pop()
print(json.dumps(raw))
''')
        self.commit("qualified full runner")

    def test_one_full_receipt_seeds_lanes_and_repeat_executes_nothing(self):
        self.assertEqual(self.gate(), "lint:run,tests:run")
        head = _git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(self.runs(), ["full"])
        outcome = run_gate(self.repo, self.config())
        self.assertEqual(outcome.exit_code, 0, outcome.message)
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual(self.runs(), ["full"], "repeat must not append another runner invocation")
        for lane in ("lint", "tests"):
            self.assertEqual(len(list((cache_dir(self.repo) / lane).glob("*.json"))), 1)

    def test_omitted_job_cannot_seed_any_lane_or_stamp(self):
        self.write("missing-tests", "yes")
        self.commit("omit required job")
        outcome = run_gate(self.repo, self.config())
        self.assertNotEqual(outcome.exit_code, 0)
        for lane in ("lint", "tests"):
            self.assertEqual(list((cache_dir(self.repo) / lane).glob("*.json")), [])
        self.assertNotIn("Local-Gate:", _git(self.repo, "log", "-1", "--format=%B"))
