"""Tracked Python import resolution without executing repository helpers."""

from __future__ import annotations

import unittest

from ci_lint.gate_python_surfaces import _imports


class PythonImportsTest(unittest.TestCase):
    def test_script_directory_and_root_modules_are_protected(self) -> None:
        tracked = {"ci/platform_host.py", "ci/evidence.py", "root_helper.py"}
        text = "import platform_host\nfrom evidence import check\nimport root_helper\nimport os\n"
        self.assertEqual(_imports(text, "ci/gate.py", tracked), tracked)

    def test_relative_imports_protect_package_initializers(self) -> None:
        tracked = {"pkg/__init__.py", "pkg/inner/__init__.py", "pkg/helper.py"}
        self.assertEqual(
            _imports("from ..helper import check\n", "pkg/inner/gate.py", tracked),
            {"pkg/__init__.py", "pkg/helper.py"},
        )
        self.assertEqual(
            _imports("from . import inner\n", "pkg/gate.py", tracked),
            {"pkg/__init__.py", "pkg/inner/__init__.py"},
        )

    def test_conditional_imports_are_protected_but_external_modules_are_not(self) -> None:
        tracked = {"ci/local.py"}
        text = "if False:\n    import local\nfrom external import missing\n"
        self.assertEqual(_imports(text, "ci/gate.py", tracked), tracked)

    def test_unparseable_source_protects_its_directory(self) -> None:
        self.assertEqual(_imports("def broken(\n", "ci/gate.py", set()), {"ci/**"})

    def test_relative_import_cannot_escape_repository(self) -> None:
        self.assertEqual(_imports("from ...helper import check\n", "pkg/gate.py", {"helper.py"}), set())


if __name__ == "__main__":
    unittest.main()
