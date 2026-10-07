"""Replay proof rejection must precede stamping or recording a lane pass."""

from __future__ import annotations

import sys
import json
import copy
import unittest
from dataclasses import replace
from unittest.mock import patch

from ci_lint.lane_cache import cache_dir
from ci_lint.local_gate import run_gate
from ci_lint.execution_pins import parse_execution_pins
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

    def provider(self):
        pins = {"interface_schema": 1, "act_version": "0.2.89-act2.11"}
        for field in ("act_binary_digest", "engine_manifest_digest", "engine_config_digest",
                      "runner_manifest_digest", "runner_config_digest"):
            pins[field] = "sha256:" + "a" * 64
        profile = self.tmp / "provider.json"
        profile.write_text(json.dumps({"runners": {"execution_pins": pins}}))
        self.write("provider.py", f"import pathlib\nprint(pathlib.Path({str(profile)!r}).read_text())\n")
        path = self.repo / "local-gate.toml"
        path.write_text(path.read_text().replace('qualified = true',
                        f'qualified = true\nprovider-query = ["{sys.executable}", "provider.py"]'))
        script = self.repo / "full.py"
        script.write_text(script.read_text().replace("print(json.dumps(raw))",
                          f'raw["execution_pins"] = json.loads(pathlib.Path({str(profile)!r}).read_text())["runners"]["execution_pins"]\n'
                          'raw["act_version"] = raw["execution_pins"]["act_version"]\nprint(json.dumps(raw))'))
        self.commit("bind effective execution provider")
        return profile

    def test_provider_change_invalidates_same_cli_and_same_tree_pass(self):
        profile = self.provider()
        self.assertEqual(self.gate(), "lint:run,tests:run")
        head = _git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(run_gate(self.repo, self.config()).exit_code, 0)
        self.assertEqual(self.runs(), ["full"])
        pins = json.loads(profile.read_text())
        pins["runners"]["execution_pins"]["act_binary_digest"] = "sha256:" + "b" * 64
        profile.write_text(json.dumps(pins))
        outcome = run_gate(self.repo, self.config())
        self.assertEqual(outcome.exit_code, 0, outcome.message)
        self.assertIn("lint:run,tests:run", outcome.message)
        self.assertEqual(self.runs(), ["full", "full"])
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD^{tree}"), _git(self.repo, "rev-parse", f"{head}^{{tree}}"))
        for lane in ("lint", "tests"):
            self.assertEqual(len(list((cache_dir(self.repo) / lane).glob("*.json"))), 2)

    def test_missing_provider_refuses_before_cached_or_forced_execution(self):
        profile = self.provider()
        self.assertEqual(self.gate(), "lint:run,tests:run")
        head = _git(self.repo, "rev-parse", "HEAD")
        profile.write_text('{"runners": {}}')
        for use_cache in (True, False):
            outcome = run_gate(self.repo, self.config(), use_cache=use_cache)
            self.assertNotEqual(outcome.exit_code, 0)
            self.assertIn("provider", outcome.message)
        self.assertEqual(self.runs(), ["full"])
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), head)

    def test_receipt_from_another_provider_caches_no_new_pass(self):
        self.provider()
        self.assertEqual(self.gate(), "lint:run,tests:run")
        script = self.repo / "full.py"
        script.write_text(script.read_text().replace('print(json.dumps(raw))',
                          'raw["execution_pins"]["runner_config_digest"] = "sha256:" + "b" * 64\nprint(json.dumps(raw))'))
        self.commit("receipt reports a different provider")
        head = _git(self.repo, "rev-parse", "HEAD")
        outcome = run_gate(self.repo, self.config())
        self.assertNotEqual(outcome.exit_code, 0)
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), head)
        for lane in ("lint", "tests"):
            self.assertEqual(len(list((cache_dir(self.repo) / lane).glob("*.json"))), 1)

    def test_provider_change_during_execution_caches_no_pass(self):
        profile = self.provider()
        script = self.repo / "full.py"
        script.write_text(script.read_text().replace('print(json.dumps(raw))',
                          'changed = json.loads(json.dumps(raw["execution_pins"]))\n'
                          'changed["runner_config_digest"] = "sha256:" + "b" * 64\n'
                          f'pathlib.Path({str(profile)!r}).write_text(json.dumps({{"runners": {{"execution_pins": changed}}}}))\n'
                          'print(json.dumps(raw))'))
        self.commit("daemon changes during execution")
        head = _git(self.repo, "rev-parse", "HEAD")
        outcome = run_gate(self.repo, self.config())
        self.assertNotEqual(outcome.exit_code, 0)
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), head)
        for lane in ("lint", "tests"):
            self.assertEqual(list((cache_dir(self.repo) / lane).glob("*.json")), [])

    def test_provider_change_during_cached_reuse_does_not_restamp(self):
        profile = self.provider()
        self.assertEqual(self.gate(), "lint:run,tests:run")
        pins = parse_execution_pins(json.loads(profile.read_text())["runners"]["execution_pins"])
        changed = replace(pins, runner_config_digest="sha256:" + "b" * 64)
        head = _git(self.repo, "rev-parse", "HEAD")
        with patch("ci_lint.workflow_replay_runtime.query_execution_pins", side_effect=[pins, pins, changed]):
            outcome = run_gate(self.repo, self.config())
        self.assertNotEqual(outcome.exit_code, 0)
        self.assertEqual(self.runs(), ["full"])
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), head)

    def test_relative_provider_script_is_mandatory_despite_exclusions(self):
        self.provider()
        config = self.repo / "local-gate.toml"
        config.write_text(config.read_text().replace('"provider.py"]', '"./provider.py"]')
                          .replace('exclude = ["docs/**"]', 'exclude = ["docs/**", "**/*.py"]'))
        self.commit("relative provider query with Python exclusions")
        self.assertEqual(self.gate(), "lint:run,tests:run")
        script = self.repo / "provider.py"
        script.write_text(script.read_text() + "# changed query implementation\n")
        self.commit("provider query changed without changed pins")
        outcome = run_gate(self.repo, self.config())
        self.assertEqual(outcome.exit_code, 0, outcome.message)
        self.assertEqual(self.runs(), ["full", "full"])
