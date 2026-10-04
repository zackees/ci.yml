"""RUST-009 -- ci_lint/runtime/toolchain_build.py (M2-22, `ci-lint rust
toolchain-build-check`): per-run toolchain/std/driver builds detected from
job-log evidence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ci_lint.finding import Status
from ci_lint.runtime.toolchain_build import (
    ToolchainBuildCheckError,
    compute_toolchain_build_report,
)


def rule_ids(findings) -> list[str]:
    return [f.rule for f in findings]


class ToolchainBuildCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_compiling_std_is_rust_009_violation(self) -> None:
        log = self._write(
            "job.log",
            "   Compiling libc v0.2.1\n"
            "   Compiling core v0.0.0 (/rustc/.../library/core)\n"
            "   Compiling demo v0.1.0\n"
            "    Finished `dev` profile\n",
        )
        report = compute_toolchain_build_report([log])
        self.assertIn("RUST-009", rule_ids(report.findings))
        self.assertTrue(any(f.status == Status.VIOLATION for f in report.findings))
        self.assertTrue(any("Compiling core" in f.message for f in report.findings))

    def test_build_std_flag_is_rust_009_violation(self) -> None:
        log = self._write("job.log", "+ cargo check -Zbuild-std --target aarch64-unknown-linux-gnu\n")
        report = compute_toolchain_build_report([log])
        self.assertIn("RUST-009", rule_ids(report.findings))
        self.assertTrue(any("-Zbuild-std" in f.message for f in report.findings))

    def test_dylint_driver_source_build_is_rust_009_violation(self) -> None:
        log = self._write("job.log", "   Compiling dylint-driver v3.1.0\n    Finished `release` profile\n")
        report = compute_toolchain_build_report([log])
        self.assertIn("RUST-009", rule_ids(report.findings))
        self.assertTrue(any(f.status == Status.VIOLATION for f in report.findings))
        self.assertTrue(any("Dylint driver was built from source" in f.message for f in report.findings))

    def test_allow_env_without_driver_build_trace_is_needs_review(self) -> None:
        log = self._write("job.log", "+ SOLDR_ALLOW_DYLINT_DRIVER_BUILD=1 soldr dylint --all -- --workspace --all-targets\n")
        report = compute_toolchain_build_report([log])
        self.assertIn("RUST-009", rule_ids(report.findings))
        self.assertTrue(all(f.status == Status.NEEDS_REVIEW for f in report.findings))

    def test_actual_cargo_driver_name_is_detected_with_ansi(self) -> None:
        log = self._write(
            "job.log",
            "\x1b[1m\x1b[92m   Compiling\x1b[0m dylint_driver v6.0.1\n"
            "SOLDR_ALLOW_DYLINT_DRIVER_BUILD=1\n",
        )
        report = compute_toolchain_build_report([log])
        self.assertEqual(["RUST-009"], rule_ids(report.findings))
        self.assertEqual(Status.VIOLATION, report.findings[0].status)

    def test_driver_name_prefix_is_not_a_driver_build(self) -> None:
        log = self._write("job.log", "   Compiling dylint_driver_support v1.0.0\n")
        self.assertEqual((), compute_toolchain_build_report([log]).findings)

    def test_clean_log_has_no_findings(self) -> None:
        log = self._write(
            "job.log",
            "   Compiling demo v0.1.0\n"
            "    Finished `dev` profile [unoptimized + debuginfo] target(s) in 1.2s\n",
        )
        report = compute_toolchain_build_report([log])
        self.assertEqual([], list(report.findings))

    def test_unreadable_log_raises(self) -> None:
        with self.assertRaises(ToolchainBuildCheckError):
            compute_toolchain_build_report([self.dir / "missing.log"])


if __name__ == "__main__":
    unittest.main()
