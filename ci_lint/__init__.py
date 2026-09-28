"""ci_lint: standard-library-only static precheck and planner for ci.toml schema 3.

Implemented in this round (round 1A of zackees/ci.yml issue #6):
  - ci_lint.schema   -- load + strictly validate ci.toml into frozen dataclasses
  - ci_lint.precheck -- run the static rule groups (CT/TAG/GEN/WF/SEC/RUN/TOOL/CACHE/
                         LAYOUT/RUST/PKG) and report findings
  - ci_lint.plan     -- compute the flow/tag selection for an event and emit plan.json

Runtime commands (units, tests, cache save-ok/heal/trim/janitor/budget execution,
audit) are later rounds. See ci_lint.runtime_stub for their placeholders.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
