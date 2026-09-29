"""Deliberately wrong lane script for the RUST-002 RED fixture: calls the
bare 'cargo-dylint' binary instead of soldr's wrapper. GEN-004's own
"follow one level into ci/*.py" convention means ci_lint's RUST-002 check
must see this subprocess.run(...) argv even though the workflow's run:
line only says 'python3 ci/dylint.py'."""

import subprocess


def main() -> int:
    subprocess.run(["cargo-dylint", "--all", "--", "--workspace", "--all-targets"], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
