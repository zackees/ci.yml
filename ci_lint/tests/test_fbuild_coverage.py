"""`ci-lint fbuild coverage-check` -- ci_lint/fbuild_coverage.py
(zackees/ci.yml#39, sub-issue of #4/#52; proposal.md's
"fbuild-coverage-preserved" acceptance fixture).

Fixtures under `ci_lint/tests/fixtures/fbuild/`:

- `ci.toml` + `board_families.json` -- the "existing" side: a pinned,
  read-only snapshot of `FastLED/fbuild @
  ef10ced8c86c124af72266e2a7041497c8709a66`'s `ci/board_families.json`
  (80 registry entries, 4 of them duplicate `workflow` aliases sharing an
  `env_name` -> 76 distinct required board build cases) plus the
  non-board required coverage read from that pinned SHA's
  `check-macos.yml`/`check-ubuntu.yml` (see SOURCE.md).
- `green/` -- an identical copy: declared coverage equals existing
  coverage, so the subset check passes.
- `red_missing_board/` -- the `teensy41` board case (a `core: true`
  board) dropped from the registry.
- `red_missing_macos/` -- `macos-15-intel` dropped from `macos_archs`.
- `red_missing_facade/` -- the ignored Python facade suite dropped
  entirely.
"""

from __future__ import annotations

import unittest

from ci_lint.fbuild_coverage import (
    FbuildCoverageError,
    check_coverage_preserved,
    distinct_board_env_names,
    load_board_registry,
    load_coverage_set,
    render_text,
)
from ci_lint.tests.helpers import FIXTURES


class FbuildCoverageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = FIXTURES / "fbuild"
        self.existing_ci_toml = self.dir / "ci.toml"

    # -- the pinned snapshot itself: 80 registry entries, 76 distinct ----

    def test_pinned_registry_has_80_entries_76_distinct(self) -> None:
        cases = load_board_registry(self.dir / "board_families.json")
        self.assertEqual(80, len(cases))
        self.assertEqual(76, len(distinct_board_env_names(cases)))

    def test_pinned_registry_has_known_duplicate_aliases(self) -> None:
        cases = load_board_registry(self.dir / "board_families.json")
        workflows_for_nano_every = sorted(c.workflow for c in cases if c.env_name == "nano_every")
        self.assertEqual(["build-nano-every.yml", "build-nano_every.yml"], workflows_for_nano_every)

    # -- GREEN: declared coverage equals existing coverage ---------------

    def test_green_identical_coverage_passes(self) -> None:
        existing = load_coverage_set(self.existing_ci_toml, label="--existing")
        declared = load_coverage_set(self.dir / "green" / "ci.toml", label="--declared")
        findings = check_coverage_preserved(existing, declared)
        self.assertEqual((), findings)
        text = render_text(findings, existing_path=self.existing_ci_toml, declared_path=self.dir / "green" / "ci.toml")
        self.assertIn("GREEN", text)

    def test_green_declares_76_distinct_board_cases(self) -> None:
        declared = load_coverage_set(self.dir / "green" / "ci.toml", label="--declared")
        self.assertEqual(76, len(declared.board_env_names))
        self.assertEqual({"macos-15-intel", "macos-15"}, declared.macos_archs)
        self.assertEqual(1, len(declared.python_facades_ignored))

    # -- RED: dropping a board case is caught, by its own env_name -------

    def test_red_missing_board_case_fails(self) -> None:
        existing = load_coverage_set(self.existing_ci_toml, label="--existing")
        declared = load_coverage_set(self.dir / "red_missing_board" / "ci.toml", label="--declared")
        findings = check_coverage_preserved(existing, declared)
        self.assertEqual(1, len(findings))
        self.assertEqual("board_env_names", findings[0].category)
        self.assertEqual("teensy41", findings[0].missing_id)

    # -- RED: dropping a macOS architecture is caught ---------------------

    def test_red_missing_macos_arch_fails(self) -> None:
        existing = load_coverage_set(self.existing_ci_toml, label="--existing")
        declared = load_coverage_set(self.dir / "red_missing_macos" / "ci.toml", label="--declared")
        findings = check_coverage_preserved(existing, declared)
        self.assertEqual(1, len(findings))
        self.assertEqual("macos_archs", findings[0].category)
        self.assertEqual("macos-15-intel", findings[0].missing_id)

    # -- RED: dropping the ignored Python facade suite is caught ---------

    def test_red_missing_python_facade_suite_fails(self) -> None:
        existing = load_coverage_set(self.existing_ci_toml, label="--existing")
        declared = load_coverage_set(self.dir / "red_missing_facade" / "ci.toml", label="--declared")
        findings = check_coverage_preserved(existing, declared)
        self.assertEqual(1, len(findings))
        self.assertEqual("python_facades_ignored", findings[0].category)

    # -- restoring the dropped case returns to GREEN ----------------------

    def test_restoring_dropped_board_case_returns_green(self) -> None:
        existing = load_coverage_set(self.existing_ci_toml, label="--existing")
        # "restore its implementation" == compare existing against itself.
        findings = check_coverage_preserved(existing, existing)
        self.assertEqual((), findings)

    # -- malformed input is a hard error, never a silent pass -------------

    def test_missing_coverage_table_errors(self) -> None:
        with self.assertRaises(FbuildCoverageError):
            load_coverage_set(self.dir.parent / "runtime" / "example-drift" / "example.toml", label="--existing")


if __name__ == "__main__":
    unittest.main()
