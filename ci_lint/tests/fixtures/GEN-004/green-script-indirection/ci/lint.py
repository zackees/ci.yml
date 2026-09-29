"""Runs the fast lane's Python quick checks; called by jobs.fast as a single
'python3 ci/lint.py' line (GEN-004's "follow one level" resolution scans
this file's own subprocess argv literals)."""

import subprocess


def main() -> int:
    subprocess.run(["ruff", "check", "."], check=True)
    subprocess.run(["ruff", "format", "--check", "."], check=True)
    subprocess.run(["pylint", "src"], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
