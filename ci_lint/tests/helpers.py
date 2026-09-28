"""Shared test helpers: fixture paths and the YAML-tooling skip guard.

Rule checks that need parsed workflow YAML (`ci_lint.workflow_scan`) only
work when PyYAML is importable or `yq` is on PATH (round-1A brief, section
B: "PyYAML if importable, else `yq -o=json`, else needs_review" -- ci_lint
itself stays standard-library-only). Locally that means:

    uv run --no-project --with pyyaml python3 -m unittest discover -s ci_lint/tests

Without either tool, `python3 -m unittest discover -s ci_lint/tests` must
still exit 0 (per the round-1A brief, section D), so every test that
depends on parsed workflow YAML is skipped -- not silently passed -- when
neither is available. Rules that do not need YAML (schema/TOML rules,
SEC-001's text scan, LAYOUT-001, the Cargo-manifest rules, the packaging
rules) are never skipped.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from ci_lint.yaml_io import yaml_tooling_available

FIXTURES = Path(__file__).parent / "fixtures"

requires_yaml_tooling = unittest.skipUnless(
    yaml_tooling_available(), "PyYAML not importable and yq not on PATH"
)


def fixture(rule: str, kind: str) -> Path:
    path = FIXTURES / rule / kind
    if not path.is_dir():
        raise FileNotFoundError(f"missing fixture {rule}/{kind} at {path}")
    return path
