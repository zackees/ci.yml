"""ci_lint.repo_files -- shared git-tracked-file enumeration.

Round-2A brief, defect 1: every text/AST scan must iterate git-tracked
files when `--repo` is inside a git work tree, and fall back to a
filesystem walk (skipping VCS/build noise) only for a non-git fixture dir.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ci_lint.proc import run_captured
from ci_lint.repo_files import list_repo_files
from ci_lint.tests.helpers import init_git_repo


class GitScopeTest(unittest.TestCase):
    def test_inside_a_git_work_tree_respects_gitignore(self) -> None:
        """Round-2A brief, defect 1: a real CI checkout of a repo like the
        template never has a materialized, gitignored `.cargo/registry/**`
        tracked in git -- but it *can* exist on disk as vendored build
        output. `list_repo_files` must use `git ls-files -co
        --exclude-standard` and skip it, the way round-1A's plain
        filesystem walk (with no `.cargo` exclusion at all) did not --
        producing 1,750 bogus LAYOUT-001 findings against the template."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_git_repo(root)
            (root / ".gitignore").write_text("target/\n.cargo/registry/\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
            (root / ".cargo" / "registry" / "src" / "some-dep-1.0.0").mkdir(parents=True)
            (root / ".cargo" / "registry" / "src" / "some-dep-1.0.0" / "lib.rs").write_text(
                "junk\n", encoding="utf-8"
            )

            files = list_repo_files(root)

        self.assertIn(".gitignore", files)
        self.assertIn("src/app.py", files)
        self.assertFalse(
            [f for f in files if f.startswith(".cargo/registry/")],
            msg=f"gitignored .cargo/registry/** leaked into the scan: {files}",
        )


class NonGitFallbackTest(unittest.TestCase):
    def test_a_bare_directory_falls_back_to_a_filesystem_walk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
            (root / "target" / "debug").mkdir(parents=True)
            (root / "target" / "debug" / "junk.rs").write_text("junk\n", encoding="utf-8")
            (root / ".cargo" / "registry").mkdir(parents=True)
            (root / ".cargo" / "registry" / "junk.rs").write_text("junk\n", encoding="utf-8")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "junk.js").write_text("junk\n", encoding="utf-8")

            files = list_repo_files(root)

        self.assertIn("src/app.py", files)
        self.assertFalse([f for f in files if f.startswith("target/")])
        self.assertFalse([f for f in files if f.startswith(".cargo/registry")])
        self.assertFalse([f for f in files if f.startswith("node_modules/")])

    def test_a_bare_directory_is_not_reported_as_a_git_work_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = run_captured(["git", "rev-parse", "--is-inside-work-tree"], cwd=root)
        # A bare tmp dir is not inside any git work tree (assuming the test
        # runner's tmp dir is not itself nested in one, which
        # tempfile.mkdtemp()'s default location never is).
        self.assertNotEqual(0, proc.returncode)


if __name__ == "__main__":
    unittest.main()
