"""`ci-lint selftest`: run ci_lint's own unittest suite (round-2A brief,
part 2e; zackees/zccache#1760 -- "agents must be able to run it").

zccache#1760's root causes included "agents were blocked from running the
contract tests". This command exists so an agent never has to guess the
right `python3 -m unittest discover ...` invocation (or hit a tool-
permission guard trying to run one directly): `python3 -m ci_lint
selftest` is the one blessed, always-available entry point, implemented
with nothing but stdlib `unittest`.
"""

from __future__ import annotations

import unittest
from pathlib import Path


def run_selftest(verbosity: int = 2) -> int:
    package_root = Path(__file__).resolve().parent  # .../ci_lint
    repo_root = package_root.parent  # the checkout containing ci_lint/

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(package_root / "tests"), top_level_dir=str(repo_root))
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1
