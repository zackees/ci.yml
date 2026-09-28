"""TOOL-001, TOOL-002, CACHE-009 -- ci_lint/rules/tools.py."""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from ci_lint.rules.tools import (
    check_cache_009,
    check_tool_rules_python,
    check_tool_rules_workflows,
    find_commands,
)
from ci_lint.schema import load_ci_toml
from ci_lint.tests.helpers import fixture, requires_yaml_tooling


@requires_yaml_tooling
class ToolFixtureTest(unittest.TestCase):
    def test_tool_001_bare_cargo(self) -> None:
        rules = [f.rule for f in check_tool_rules_workflows(fixture("TOOL-001", "red"))]
        self.assertIn("TOOL-001", rules)
        rules = [f.rule for f in check_tool_rules_workflows(fixture("TOOL-001", "green"))]
        self.assertNotIn("TOOL-001", rules)

    def test_tool_002_missing_locked(self) -> None:
        rules = [f.rule for f in check_tool_rules_workflows(fixture("TOOL-002", "red"))]
        self.assertIn("TOOL-002", rules)
        rules = [f.rule for f in check_tool_rules_workflows(fixture("TOOL-002", "green"))]
        self.assertNotIn("TOOL-002", rules)

    def test_cache_009_wrapper_misconfigured(self) -> None:
        repo = fixture("CACHE-009", "red")
        ci, _ = load_ci_toml(repo)
        self.assertIn("CACHE-009", [f.rule for f in check_cache_009(ci, repo)])
        repo = fixture("CACHE-009", "green")
        ci, _ = load_ci_toml(repo)
        self.assertNotIn("CACHE-009", [f.rule for f in check_cache_009(ci, repo)])


class ToolUnitTest(unittest.TestCase):
    """Fast, YAML-independent checks on the plumbing itself."""

    def test_find_commands_strips_env_and_splits_control_ops(self) -> None:
        cmds = find_commands("FOO=bar cargo build --locked && soldr wheel build")
        self.assertEqual([["cargo", "build", "--locked"], ["soldr", "wheel", "build"]], cmds)

    def test_soldr_cargo_still_checked_for_locked(self) -> None:
        from ci_lint.rules.tools import _tool_findings_for_commands

        findings = _tool_findings_for_commands([["soldr", "cargo", "build"]], "x.yml", "loc")
        self.assertEqual(["TOOL-002"], [f.rule for f in findings])

    def test_bare_cargo_with_locked_still_flags_tool_001(self) -> None:
        from ci_lint.rules.tools import _tool_findings_for_commands

        findings = _tool_findings_for_commands([["cargo", "build", "--locked"]], "x.yml", "loc")
        self.assertEqual(["TOOL-001"], [f.rule for f in findings])

    def test_ast_scan_finds_subprocess_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ci_dir = root / "ci"
            ci_dir.mkdir()
            (ci_dir / "build.py").write_text(
                "import subprocess\n"
                "subprocess.run(['cargo', 'build'])\n",
                encoding="utf-8",
            )
            findings = check_tool_rules_python(root)
            self.assertIn("TOOL-001", [f.rule for f in findings])
            self.assertTrue(all(f.line is not None for f in findings))

    def test_python_ast_helpers_are_syntax_valid(self) -> None:
        # Smoke-check the AST-walking module itself parses cleanly.
        import ci_lint.rules.tools as mod

        ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
