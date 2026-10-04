"""Replay proof rejection must precede stamping or recording a lane pass."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace

from ci_lint.lane_cache import cache_dir
from ci_lint.local_gate import run_gate
from ci_lint.tests.test_lane_cache import LanedRepo, _git
from ci_lint.tests.helpers import requires_yaml_tooling
from ci_lint.tests import test_lane_cache

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
