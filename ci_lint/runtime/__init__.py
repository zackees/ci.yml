"""Runtime commands: `ci-lint units`, `ci-lint tests size`, `ci-lint wheel
check|installed`, `ci-lint gate`. These run *after* a build (unlike
`ci_lint.rules.*`, which are all static precheck rules) -- against
`cargo --message-format=json` output, built wheels, an installed venv, and
GitHub's `toJSON(needs)` -- so they live in their own package rather than
`ci_lint.rules`.
"""

from __future__ import annotations
