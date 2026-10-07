"""Provider identity never substitutes for executed-check evidence."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from ci_lint.execution_pins import ExecutionPins, parse_execution_pins
from ci_lint.workflow_replay_config import ReplayConfig
from ci_lint.workflow_replay_runtime import bind_execution_pins


class ExecutionPinsTest(unittest.TestCase):
    def setUp(self):
        self.pins = ExecutionPins(1, "0.2.89-act2.11", *("sha256:" + "a" * 64 for _ in range(5)))

    def test_each_field_is_required_and_strictly_typed(self):
        raw = asdict(self.pins)
        self.assertEqual(parse_execution_pins(raw), self.pins)
        for field in raw:
            with self.subTest(field=field):
                missing = dict(raw)
                missing.pop(field)
                with self.assertRaises(ValueError):
                    parse_execution_pins(missing)
                invalid = dict(raw)
                invalid[field] = True
                with self.assertRaises(ValueError):
                    parse_execution_pins(invalid)
        for invalid in ({**raw, "extra": 1}, {**raw, "interface_schema": 2},
                        {**raw, "act_version": "0.2.89-act2.10"},
                        {**raw, "act_binary_digest": "sha256:" + "A" * 64}):
            with self.assertRaises(ValueError):
                parse_execution_pins(invalid)

    def test_query_refuses_duplicate_failed_and_changed_provider(self):
        good = json.dumps({"runners": {"execution_pins": asdict(self.pins)}})
        config = ReplayConfig("owner/repo", ".github/workflows/ci.yml", "minimal", ())
        with tempfile.TemporaryDirectory() as scratch:
            repo = Path(scratch)
            queried = replace(config, provider_query=(sys.executable, "-c", f"print({good!r})"))
            bound = bind_execution_pins(repo, queried)
            self.assertEqual(bound.execution_pins, self.pins)
            self.assertEqual(bind_execution_pins(repo, bound), bound)
            changed = replace(bound, execution_pins=replace(self.pins, runner_config_digest="sha256:" + "b" * 64))
            with self.assertRaisesRegex(ValueError, "changed"):
                bind_execution_pins(repo, changed)
            duplicate = '{"runners": {}, "runners": {"execution_pins": ' + json.dumps(asdict(self.pins)) + '}}'
            for program in (f"print({duplicate!r})", "raise SystemExit(2)", "print('x' * 65537)"):
                with self.assertRaises(ValueError):
                    bind_execution_pins(repo, replace(queried, provider_query=(sys.executable, "-c", program)))
