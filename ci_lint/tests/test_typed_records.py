"""PY-002 -- ci_lint/typed_records.py, and the ratchet over ci_lint's own source."""

from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

from ci_lint.typed_records import scan_source, scan_tree

ROOT = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).with_name("typed_records_baseline.json")


class ScanTest(unittest.TestCase):
    def _where(self, src: str) -> list[str]:
        return [v.where for v in scan_source("m.py", src)]

    def test_heterogeneous_tuple_return_is_reported(self) -> None:
        self.assertEqual(self._where("def f() -> tuple[int, list[str], int]: ..."), ["return of f()"])
        self.assertEqual(self._where("def f() -> tuple[int, str] | None: ..."), ["return of f()"])

    def test_homogeneous_tuple_and_plain_types_are_fine(self) -> None:
        self.assertEqual(self._where("def f() -> tuple[str, ...]: ...\ndef g() -> list[int]: ..."), [])

    def test_dict_return_and_dataclass_field(self) -> None:
        src = (
            "from dataclasses import dataclass\n"
            "def f() -> dict[str, int]: ...\n"
            "@dataclass(frozen=True)\nclass R:\n    a: dict[str, str]\n    b: tuple[int, int]\n    c: list[int]\n"
        )
        self.assertEqual(self._where(src), ["return of f()", "field R.a", "field R.b"])

    def test_wire_boundary_dicts_and_parameters_are_allowed(self) -> None:
        src = (
            "def f(env: dict[str, str]) -> dict[str, JsonValue]: ...\n"
            "def g() -> dict[str, YamlValue] | None: ...\n"
        )
        self.assertEqual(self._where(src), [])

    def test_plain_class_fields_are_not_scanned(self) -> None:
        self.assertEqual(self._where("class C:\n    a: dict[str, int]\n"), [])


class RatchetTest(unittest.TestCase):
    def test_no_module_exceeds_its_baseline(self) -> None:
        baseline: dict[str, int] = json.loads(BASELINE.read_text(encoding="utf-8"))["counts"]
        current = Counter(v.path for v in scan_tree(ROOT, "ci_lint"))
        grown = {
            path: (count, baseline.get(path, 0)) for path, count in current.items() if count > baseline.get(path, 0)
        }
        self.assertEqual(
            grown, {},
            "PY-002: use a typed dataclass instead of a record-shaped tuple/dict "
            f"(module: (now, allowed)) -- {grown}\n"
            + "\n".join(v.render() for v in scan_tree(ROOT, "ci_lint") if v.path in grown),
        )

    def test_baseline_is_tight(self) -> None:
        baseline: dict[str, int] = json.loads(BASELINE.read_text(encoding="utf-8"))["counts"]
        current = Counter(v.path for v in scan_tree(ROOT, "ci_lint"))
        loose = {path: (current.get(path, 0), allowed) for path, allowed in baseline.items() if current.get(path, 0) < allowed}
        self.assertEqual(loose, {}, f"PY-002 ratchet: lower these baseline entries to the current count -- {loose}")


if __name__ == "__main__":
    unittest.main()
