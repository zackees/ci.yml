"""PY-003 -- ci_lint/subprocess_capture.py, ci_lint/proc.py, `ci-lint py lint`."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from ci_lint.proc import run_captured
from ci_lint.py_lint import counts_of, load_baseline, ratchet, scan, tracked_python, write_baseline
from ci_lint.subprocess_capture import scan_source

ROOT = Path(__file__).resolve().parents[2]


class ScanTest(unittest.TestCase):
    def _reasons(self, src: str) -> list[str]:
        return [v.reason for v in scan_source("m.py", "import subprocess\nfrom subprocess import PIPE\n" + src)]

    def test_banned_forms(self) -> None:
        src = (
            "subprocess.run(['a'], capture_output=True)\n"
            "subprocess.Popen(['a'], stdout=subprocess.PIPE)\n"
            "subprocess.run(['a'], stderr=PIPE)\n"
            "subprocess.check_output(['a'])\n"
            "subprocess.getoutput('a')\n"
            "asyncio.create_subprocess_exec('a', stdout=asyncio.subprocess.PIPE)\n"
        )
        self.assertEqual(
            self._reasons(src),
            [
                "uses capture_output (pipes)",
                "sets stdout=PIPE",
                "sets stderr=PIPE",
                "always captures through a pipe",
                "always captures through a pipe",
                "sets stdout=PIPE",
            ],
        )

    def test_allowed_forms(self) -> None:
        src = (
            "subprocess.run(['a'], stdout=subprocess.DEVNULL)\n"
            "subprocess.run(['a'], stdout=fh, stderr=subprocess.STDOUT)\n"
            "subprocess.run(['a'], capture_output=False)\n"
            "pool.run(job, capture_output=True)\n"
            "subprocess.Popen(['a'], stdin=PIPE)  # ci-lint: allow PY-003 interactive protocol, drained concurrently\n"
        )
        self.assertEqual(self._reasons(src), [])

    def test_ci_lint_itself_has_no_pipe_capture(self) -> None:
        offenders = [
            v.render()
            for f in sorted((ROOT / "ci_lint").rglob("*.py"))
            if "/tests/fixtures/" not in f.as_posix()  # other repos' sample data
            for v in scan_source(f.relative_to(ROOT).as_posix(), f.read_text(encoding="utf-8"))
        ]
        self.assertEqual(offenders, [], "PY-003: use ci_lint.proc.run_captured")


class RunCapturedTest(unittest.TestCase):
    def test_large_output_and_exit_code(self) -> None:
        r = run_captured(["sh", "-c", "head -c 3000000 /dev/zero | tr '\\0' x; echo err >&2; exit 3"])
        self.assertEqual((r.returncode, len(r.stdout), r.stderr.strip()), (3, 3000000, "err"))

    def test_input_text(self) -> None:
        self.assertEqual(run_captured(["cat"], input_text="hello").stdout, "hello")

    def test_a_grandchild_holding_the_output_does_not_block_the_caller(self) -> None:
        # The exact pipe hang: with capture_output=True this waits ~20 s for
        # the backgrounded grandchild to close the inherited pipe.
        start = time.monotonic()
        r = run_captured(["sh", "-c", "(sleep 20 &) ; echo done"])
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual(r.stdout.strip(), "done")


class RatchetTest(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not on PATH")
        self.tmp = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(self.tmp)], check=True)  # inherits stdout; no capture
        (self.tmp / "a.py").write_text("import subprocess\nsubprocess.run(['x'], capture_output=True)\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.tmp), "add", "a.py"], check=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _result(self, baseline: Path):  # type: ignore[no-untyped-def]
        findings = scan(self.tmp, ("PY-003",), tracked_python(self.tmp))
        return ratchet(findings, load_baseline(baseline) if baseline.is_file() else [], ("PY-003",))

    def test_baseline_ratchets_down_only(self) -> None:
        baseline = self.tmp / "baseline.json"
        self.assertEqual(len(self._result(baseline).grown), 1)  # no baseline: everything must be 0
        write_baseline(baseline, counts_of(scan(self.tmp, ("PY-003",), tracked_python(self.tmp))), ("PY-003",))
        clean = self._result(baseline)
        self.assertEqual((clean.grown, clean.loose), ((), ()))
        (self.tmp / "a.py").write_text("import subprocess\n", encoding="utf-8")
        self.assertEqual(len(self._result(baseline).loose), 1)  # fixed a site: the baseline must be lowered
        (self.tmp / "a.py").write_text(
            "import subprocess\nsubprocess.run(['x'], capture_output=True)\nsubprocess.check_output(['y'])\n",
            encoding="utf-8",
        )
        self.assertEqual(len(self._result(baseline).grown), 1)  # grew: fails


if __name__ == "__main__":
    unittest.main()
