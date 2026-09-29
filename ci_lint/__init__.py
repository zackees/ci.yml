"""ci_lint: standard-library-only static precheck and planner for ci.toml schema 3.

  - ci_lint.schema         -- load + strictly validate ci.toml into frozen dataclasses
  - ci_lint.precheck       -- run the static rule groups (CT/TAG/GEN/WF/SEC/RUN/TOOL/
                               CACHE/LAYOUT/RUST/PKG) and report findings
  - ci_lint.plan           -- compute the flow/tag selection for an event, emit plan.json
  - ci_lint.runtime.*      -- units, tests size, wheel check/installed, the CI OK gate
  - ci_lint.cache.*        -- cache key/save-ok/audit/trim/janitor/heal/preprune/delta
  - ci_lint.settings_audit -- `ci-lint audit`: live settings audit (SEC-005/006/007,
                               GEN-006/011) -- distinct from `ci_lint.cache.audit`
  - ci_lint.publish_oidc   -- `ci-lint publish oidc-check`: mock-publish OIDC identity proof
  - ci_lint.release        -- `ci-lint release verify`: staged release-candidate artifacts
  - ci_lint.perf           -- `ci-lint perf compare`: benchmark baseline/current comparison
  - ci_lint.selftest       -- run this package's own unittest suite
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
