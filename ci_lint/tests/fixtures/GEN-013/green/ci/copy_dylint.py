"""GEN-013 green fixture: a single-file copy, plus one tree copy with the
documented same-line exception, never onto a restored target/Dylint path."""

from __future__ import annotations

import shutil


def main() -> None:
    shutil.copyfile("staged/dylint/driver", "prepared/driver")
    # This tree copy is required to stage a fresh (never-restored) fixture
    # directory before the run; nothing here is ever restored from cache.
    shutil.copytree("staged/dylint", "prepared/dylint", dirs_exist_ok=True)  # ci-lint: allow GEN-013 staging only, never restored


if __name__ == "__main__":
    main()
