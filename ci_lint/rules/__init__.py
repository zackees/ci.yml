"""Precheck rule groups, cheapest first (round-1A brief, section B).

Each module is one group and exposes plain `check_*(...) -> list[Finding]`
functions so both `ci_lint.precheck` and the per-rule fixture tests can call
them directly without going through the full CLI.
"""
