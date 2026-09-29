"""Correct lane script for the RUST-002 GREEN fixture: every invocation
goes through 'soldr dylint', never a bare cargo-dylint."""

import subprocess


def main() -> int:
    subprocess.run(["soldr", "dylint", "prepare", "--target", "aarch64-apple-darwin"], check=True)
    subprocess.run(
        ["soldr", "dylint", "--all", "--", "--workspace", "--all-targets", "--target", "aarch64-apple-darwin"],
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
