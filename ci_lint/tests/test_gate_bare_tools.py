"""RUST-001 on local-gate surfaces -- ci_lint/gate_bare_tools.py (zackees/ci.yml#170)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from ci_lint.gate_bare_tools import check_no_bare_rust
from ci_lint.tests.helpers import requires_yaml_tooling

GATE = ("python3", "ci/gate.py")


class BareRustTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        (self.tmp / "ci").mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel: str, text: str) -> None:
        (self.tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.tmp / rel).write_text(text, encoding="utf-8")

    def test_not_a_rust_repo(self) -> None:
        (self.tmp / "Cargo.toml").unlink()
        self._write("ci/gate.py", 'X = ("cargo", "test")\n')
        self.assertEqual(check_no_bare_rust(self.tmp, GATE), [])

    def test_gate_script_argv(self) -> None:
        self._write("ci/gate.py", 'A = ("soldr", "cargo", "clippy")\nB = ["cargo", "test"]\n')
        findings = check_no_bare_rust(self.tmp, GATE)
        self.assertEqual([(f.rule, f.line) for f in findings], [("RUST-001", 2)])

    def test_bare_gate_argv(self) -> None:
        self.assertEqual([f.rule for f in check_no_bare_rust(self.tmp, ("cargo", "test"))], ["RUST-001"])

    def test_bosn_task_cmd(self) -> None:
        self._write("ci/gate.py", "")
        self._write("bosn.toml", '[task.a]\ncmd = "cd /repo && soldr cargo fmt"\n[task.b]\ncmd = "cd /repo && cargo test"\n')
        findings = check_no_bare_rust(self.tmp, GATE)
        self.assertEqual(len(findings), 1)
        self.assertIn("task 'b'", findings[0].message)

    @requires_yaml_tooling
    def test_workflow_lines_allow_comment_and_soldr_argument(self) -> None:
        self._write("ci/gate.py", "")
        self._write(
            ".github/workflows/ci.yml",
            "on: [pull_request]\njobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps:\n"
            "      - run: |\n"
            "          rustup toolchain install 1.98.1\n"
            "          cargo build -p x  # ci-lint: allow RUST-001 bootstrap: no soldr exists yet\n"
            '          real=$("$SOLDR" rustup which cargo)\n'
            "          soldr cargo test\n",
        )
        findings = check_no_bare_rust(self.tmp, GATE)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("rustup toolchain install", findings[0].message)
        self.assertEqual(check_no_bare_rust(self.tmp, GATE, workflows=False), [])


if __name__ == "__main__":
    unittest.main()


class FalsePositiveTest(unittest.TestCase):
    def test_assignment_and_python_steps_are_not_commands(self) -> None:
        from ci_lint.gate_bare_tools import _bare, _python_findings
        self.assertIsNone(_bare(["cargo", "=", "tomllib.loads(x)"]))
        self.assertEqual(_bare(["cargo", "build"]), "cargo")
        self.assertEqual(_python_findings('cargo = tomllib.loads(text)\nprint(cargo["package"])\n', "wf.yml"), [])
