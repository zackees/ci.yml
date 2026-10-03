"""RUST-018 / PY-004 -- ci_lint/rules/complexity.py (zackees/ci.yml#229):
the function-complexity ratchet. clippy's `cognitive_complexity` and
`too_many_lines`, and ruff's C901, at their default ceilings; existing
offenders excused one function at a time (`#[expect]`, `# noqa: C901`),
never wholesale."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from ci_lint.finding import Finding
from ci_lint.proc import run_captured
from ci_lint.rules.complexity import (
    PY_RULE,
    RUST_RULE,
    check_complexity,
    check_py_004,
    check_rust_018,
    scan_python_source,
    scan_rust_source,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

GOOD_WORKSPACE = """\
[workspace]
members = ["crates/*"]

[workspace.lints.clippy]
cognitive_complexity = "warn"
too_many_lines = { level = "deny", priority = 1 }
"""

MEMBER_INHERITS = """\
[package]
name = "a"
version = "0.1.0"

[lints]
workspace = true
"""

MEMBER_OWN_LINTS = """\
[package]
name = "b"
version = "0.1.0"

[lints.clippy]
unwrap_used = "deny"
"""

GOOD_RUFF = """\
[tool.ruff.lint]
select = ["E", "F", "C90", "RUF"]
"""


class _Repo:
    """A throwaway on-disk repository (not a git work tree, so
    `list_repo_files` walks it)."""

    def __init__(self, files: dict[str, str]) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        for rel, text in files.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(text), encoding="utf-8")

    def __enter__(self) -> Path:
        return self.root

    def __exit__(self, *exc: object) -> None:
        self._tmp.cleanup()


def _where(findings: tuple[Finding, ...] | list[Finding]) -> list[str]:
    return [f.location() for f in findings]


class Rust018Test(unittest.TestCase):
    def test_green_workspace_with_inheriting_member_and_expect(self) -> None:
        src = """\
        #[expect(clippy::too_many_lines, reason = "ratchet baseline")]
        fn big() {}
        """
        with _Repo({"Cargo.toml": GOOD_WORKSPACE, "crates/a/Cargo.toml": MEMBER_INHERITS, "crates/a/src/lib.rs": src}) as root:
            scan = check_rust_018(root)
        self.assertEqual((), scan.findings)
        self.assertEqual(1, scan.excused)

    def test_missing_workspace_lints_and_non_inheriting_member(self) -> None:
        with _Repo({"Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n', "crates/b/Cargo.toml": MEMBER_OWN_LINTS}) as root:
            scan = check_rust_018(root)
        self.assertEqual(["Cargo.toml", "Cargo.toml", "crates/b/Cargo.toml"], _where(scan.findings))
        self.assertTrue(all(f.rule == RUST_RULE for f in scan.findings))

    def test_allow_level_is_not_on(self) -> None:
        manifest = GOOD_WORKSPACE.replace('cognitive_complexity = "warn"', 'cognitive_complexity = "allow"')
        with _Repo({"Cargo.toml": manifest}) as root:
            scan = check_rust_018(root)
        self.assertEqual(1, len(scan.findings))
        self.assertIn("cognitive_complexity", scan.findings[0].message)

    def test_single_package_uses_lints_clippy(self) -> None:
        manifest = '[package]\nname = "x"\nversion = "0.1.0"\n\n[lints.clippy]\ncognitive_complexity = "warn"\ntoo_many_lines = "warn"\n'
        with _Repo({"Cargo.toml": manifest}) as root:
            self.assertEqual((), check_rust_018(root).findings)

    def test_raised_clippy_toml_threshold(self) -> None:
        files = {"Cargo.toml": GOOD_WORKSPACE, "clippy.toml": "too-many-lines-threshold = 300\ncognitive-complexity-threshold = 20\n"}
        with _Repo(files) as root:
            scan = check_rust_018(root)
        self.assertEqual(["clippy.toml"], _where(scan.findings))
        self.assertIn("too-many-lines-threshold = 300", scan.findings[0].message)

    def test_allow_attributes_are_violations(self) -> None:
        src = """\
        #![allow(clippy::pedantic)]
        #[allow(clippy::too_many_lines)]
        fn a() {}
        #[cfg_attr(test, allow(clippy::cognitive_complexity))]
        fn b() {}
        #[allow(clippy::unwrap_used)]
        fn c() {}
        // #[allow(clippy::too_many_lines)]
        const S: &str = "#[allow(clippy::too_many_lines)]";
        """
        scan = scan_rust_source(textwrap.dedent(src), "src/lib.rs")
        self.assertEqual(["src/lib.rs:1", "src/lib.rs:2", "src/lib.rs:4"], _where(scan.findings))
        self.assertEqual(0, scan.excused)

    def test_not_a_rust_repo(self) -> None:
        with _Repo({"README.md": "hi\n"}) as root:
            self.assertEqual((), check_rust_018(root).findings)


class Py004Test(unittest.TestCase):
    def test_green_prefix_selection_and_line_noqa(self) -> None:
        src = "def f(x):  # noqa: C901 -- ratchet baseline\n    return x\n"
        with _Repo({"pyproject.toml": GOOD_RUFF, "pkg/m.py": src}) as root:
            scan = check_py_004(root)
        self.assertEqual((), scan.findings)
        self.assertEqual(1, scan.excused)

    def test_no_ruff_config(self) -> None:
        with _Repo({"pkg/m.py": "x = 1\n"}) as root:
            scan = check_py_004(root)
        self.assertEqual(["pyproject.toml"], _where(scan.findings))
        self.assertIn("no ruff configuration", scan.findings[0].message)

    def test_unselected_ignored_waived_and_raised(self) -> None:
        pyproject = """\
        [tool.ruff.lint]
        select = ["ALL"]
        ignore = ["RUF100"]

        [tool.ruff.lint.per-file-ignores]
        "scripts/*.py" = ["C90"]

        [tool.ruff.lint.mccabe]
        max-complexity = 15
        """
        with _Repo({"pyproject.toml": pyproject, "m.py": "x = 1\n"}) as root:
            messages = [f.message for f in check_py_004(root).findings]
        self.assertEqual(3, len(messages), messages)
        self.assertIn("ruff ignores `RUF100` repo-wide", messages)
        self.assertTrue(any("scripts/*.py" in m for m in messages))
        self.assertTrue(any("max-complexity = 15" in m for m in messages))

    def test_missing_c901_in_ruff_toml(self) -> None:
        with _Repo({"ruff.toml": '[lint]\nselect = ["E", "RUF"]\n', "m.py": "x = 1\n"}) as root:
            findings = check_py_004(root).findings
        self.assertEqual(["ruff does not select `C901`"], [f.message for f in findings])
        self.assertEqual("ruff.toml", findings[0].path)

    def test_file_level_waivers(self) -> None:
        src = '"""Mentions # noqa: C901 and # ruff: noqa in prose only."""\n# ruff: noqa\n# ruff: noqa: E501\n# flake8: noqa: C90\n'
        scan = scan_python_source(src, "m.py")
        self.assertEqual(["m.py:2", "m.py:4"], _where(scan.findings))
        self.assertEqual(0, scan.excused)
        self.assertTrue(all(f.rule == PY_RULE for f in scan.findings))


class SelfTest(unittest.TestCase):
    """ci_lint holds itself to PY-004."""

    def test_this_repository_has_no_violations(self) -> None:
        report = check_complexity(REPO_ROOT)
        self.assertEqual([], [f.render() for f in report.findings])

    def test_ruff_passes_on_this_repository(self) -> None:
        # The local gate runs the selftest with ruff installed (local-gate.toml);
        # ci_lint itself stays standard-library-only, so elsewhere this skips.
        if importlib.util.find_spec("ruff") is None:
            self.skipTest("ruff is not installed in this interpreter")
        proc = run_captured([sys.executable, "-m", "ruff", "check", "--no-cache", str(REPO_ROOT)], cwd=REPO_ROOT, timeout=120)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
