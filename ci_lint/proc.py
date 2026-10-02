"""Run a child process and capture its output without a pipe (PY-003).

`subprocess.run(..., capture_output=True)`, `stdout=PIPE` and
`check_output` connect the child to the parent through OS pipes. Two
failure modes follow, both seen in the fleet:

1. **A full pipe blocks the child.** A pipe holds ~64 KiB; whoever stops
   draining it (a `Popen` that never calls `communicate`, a reader waiting
   on the other stream) leaves the child blocked forever on its next write.
2. **An inherited pipe keeps the parent waiting.** `run()` reads until EOF,
   and EOF only arrives when *every* holder of the write end has closed it.
   A daemon or grandchild that inherits the pipe (soldr's broker, a build
   server) keeps it open long after the direct child exited, and the
   caller hangs.

`run_captured` writes the child's stdout/stderr to anonymous temporary
files instead. A file never fills, nothing waits for EOF, and the call
returns as soon as the direct child exits -- a lingering grandchild can
keep writing to the file without blocking anyone. Input, when given, is
also fed from a temporary file.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Captured:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_captured(
    argv: Sequence[str],
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
    timeout: float | None = None,
) -> Captured:
    """Run `argv` to completion with stdout/stderr captured through
    temporary files (never pipes). Raises `subprocess.TimeoutExpired` like
    `subprocess.run` when `timeout` elapses (the child is killed first)."""

    with (
        tempfile.TemporaryFile() as out,
        tempfile.TemporaryFile() as err,
        tempfile.TemporaryFile() as stdin_file,
    ):
        stdin: int | object = subprocess.DEVNULL
        if input_text is not None:
            stdin_file.write(input_text.encode())
            stdin_file.seek(0)
            stdin = stdin_file
        proc = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdin=stdin,  # type: ignore[arg-type]
            stdout=out,
            stderr=err,
            timeout=timeout,
            check=False,
        )
        out.seek(0)
        err.seek(0)
        return Captured(
            returncode=proc.returncode,
            stdout=out.read().decode("utf-8", errors="replace"),
            stderr=err.read().decode("utf-8", errors="replace"),
        )

