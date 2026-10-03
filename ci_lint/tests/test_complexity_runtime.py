"""Runtime coverage includes files a repository's narrow lint omitted."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_lint.proc import Captured


class PythonRuntimeTest(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("ruff"), "Ruff runtime is optional")
    def test_real_ruff_checks_discovery_excluded_installer(self) -> None:
        from ci_lint.complexity_runtime import check_python_runtime

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "installer").mkdir()
            (root / "pyproject.toml").write_text(
                '[tool.ruff]\nforce-exclude = true\nexclude = ["installer"]\n'
                '[tool.ruff.lint]\nselect = ["C901", "RUF100"]\n',
                encoding="utf-8",
            )
            body = "def install(value):\n" + "".join(f"    if value == {i}:\n        print(value)\n" for i in range(12))
            (root / "installer" / "setup.py").write_text(body, encoding="utf-8")
            result = check_python_runtime(root)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("C901", result.stdout)
        self.assertIn("installer/setup.py", result.stdout)

    def test_explicit_inventory_bypasses_excludes_and_retains_configured_rules(self) -> None:
        from ci_lint.complexity_runtime import check_python_runtime

        with (
            patch(
                "ci_lint.complexity_runtime.list_repo_files", return_value=["src/a.py", "installer/b.py", "README.md"]
            ),
            patch("ci_lint.complexity_runtime.run_captured", return_value=Captured(0, "", "")) as run,
        ):
            result = check_python_runtime(Path("/repo"))
        self.assertEqual(0, result.returncode)
        argv = run.call_args.args[0]
        self.assertIn("--no-force-exclude", argv)
        self.assertIn("--extend-select", argv)
        self.assertIn("C901,RUF100", argv)
        self.assertEqual(["src/a.py", "installer/b.py"], argv[-2:])
        self.assertEqual(Path("/repo"), run.call_args.kwargs["cwd"])

    def test_failure_output_and_status_are_preserved(self) -> None:
        from ci_lint.complexity_runtime import check_python_runtime

        with (
            patch("ci_lint.complexity_runtime.list_repo_files", return_value=["bench/hot.py"]),
            patch("ci_lint.complexity_runtime.run_captured", return_value=Captured(1, "bench/hot.py: C901\n", "")),
        ):
            result = check_python_runtime(Path("/repo"))
        self.assertEqual(1, result.returncode)
        self.assertIn("bench/hot.py", result.stdout)

    def test_no_python_does_not_invoke_ruff(self) -> None:
        from ci_lint.complexity_runtime import check_python_runtime

        with (
            patch("ci_lint.complexity_runtime.list_repo_files", return_value=["Cargo.toml"]),
            patch("ci_lint.complexity_runtime.run_captured") as run,
        ):
            result = check_python_runtime(Path("/repo"))
        self.assertEqual(0, result.returncode)
        run.assert_not_called()
