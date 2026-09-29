"""TOOL-001, TOOL-002, CACHE-009, GEN-004 -- ci_lint/rules/tools.py."""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from ci_lint.finding import Status
from ci_lint.rules.tools import (
    check_cache_009,
    check_gen_004,
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

    def test_cache_009_require_plan_needs_a_plan_derived_expression(self) -> None:
        """Round-4B: [allow].setup-soldr.require.save-cache = "plan" means
        the wrapper's actual input must contain 'needs.precheck.outputs.'
        or pass through an 'inputs.*' value -- a literal (even one that
        matches some other require entry's style, like "auto") is CACHE-009."""

        repo = fixture("CACHE-009", "red-plan-save-cache")
        ci, _ = load_ci_toml(repo)
        self.assertIn("CACHE-009", [f.rule for f in check_cache_009(ci, repo)])

        repo = fixture("CACHE-009", "green-plan-save-cache")
        ci, _ = load_ci_toml(repo)
        self.assertNotIn("CACHE-009", [f.rule for f in check_cache_009(ci, repo)])

    def test_gen_004_fast_job_missing_ruff_pylint(self) -> None:
        findings = check_gen_004(fixture("GEN-004", "red"))
        self.assertIn("GEN-004", [f.rule for f in findings])
        self.assertTrue(all(f.status == Status.VIOLATION for f in findings))

    def test_gen_004_fast_job_runs_ruff_and_pylint_directly(self) -> None:
        findings = check_gen_004(fixture("GEN-004", "green"))
        self.assertEqual([], findings)

    def test_gen_004_follows_one_level_into_a_ci_script(self) -> None:
        """jobs.fast calls 'python3 ci/lint.py'; ci/lint.py's own
        subprocess.run(...) argv literals are scanned one level deep."""

        findings = check_gen_004(fixture("GEN-004", "green-script-indirection"))
        self.assertEqual([], findings)

    def test_gen_004_black_isort_are_violations_even_alongside_ruff(self) -> None:
        findings = check_gen_004(fixture("GEN-004", "red-black-isort"))
        rules = [f.rule for f in findings]
        self.assertIn("GEN-004", rules)
        messages = " ".join(f.message for f in findings)
        self.assertIn("black", messages)
        self.assertIn("isort", messages)

    def test_gen_004_unresolved_script_reference_is_needs_review_not_pass(self) -> None:
        findings = check_gen_004(fixture("GEN-004", "needs-review-unresolved-script"))
        self.assertIn("GEN-004", [f.rule for f in findings])
        self.assertTrue(any(f.status == Status.NEEDS_REVIEW for f in findings), msg=findings)
        self.assertFalse(any(f.status == Status.VIOLATION for f in findings), msg=findings)


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
