"""GEN-013 red fixture: a Python helper a workflow's run: step delegates to,
copying a directory tree over a restored Dylint output path."""

from __future__ import annotations

import shutil


def main() -> None:
    shutil.copytree("staged/dylint", "target/dylint/libraries", dirs_exist_ok=True)


if __name__ == "__main__":
    main()
