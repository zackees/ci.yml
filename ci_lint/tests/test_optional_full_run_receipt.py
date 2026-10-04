"""Optional host lanes must remain explicit and unproved in full-run receipts."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ci_lint.full_run_receipt import load_receipt

TREE = "a" * 40


class OptionalFullRunReceiptTest(unittest.TestCase):
    def test_optional_na_is_explicit_and_is_not_a_pass(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "receipt.json"
            path.write_text(json.dumps({
                "version": 1, "tree": TREE,
                "passes": [{"lane": "lint", "secs": 1}, {"lane": "tests", "secs": 2}],
                "not-applicable": ["winvm"],
            }), encoding="utf-8")
            result = load_receipt(path, tree=TREE, expected_lanes=("lint", "tests", "winvm"),
                                  optional_lanes=("winvm",))
            self.assertIsNotNone(result.evidence, result.error)
            assert result.evidence is not None
            self.assertEqual(tuple(p.lane for p in result.evidence.passes), ("lint", "tests"))
            self.assertEqual(result.evidence.not_applicable, ("winvm",))

    def test_required_na_and_missing_optional_proof_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "receipt.json"
            for absent in (["tests", "winvm"], [], ["winvm", "winvm"], ["unknown", "winvm"]):
                with self.subTest(absent=absent):
                    path.write_text(json.dumps({
                        "version": 1, "tree": TREE,
                        "passes": [{"lane": "lint", "secs": 1}, {"lane": "tests", "secs": 2}],
                        "not-applicable": absent,
                    }), encoding="utf-8")
                    result = load_receipt(path, tree=TREE, expected_lanes=("lint", "tests", "winvm"),
                                          optional_lanes=("winvm",))
                    self.assertIsNone(result.evidence)
