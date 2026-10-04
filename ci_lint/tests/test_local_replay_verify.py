"""A local workflow creates proof; its verifier must never skip its checks."""

from __future__ import annotations

import io
import os
from contextlib import redirect_stdout
from dataclasses import dataclass
from unittest.mock import patch

from ci_lint.cli import main
from ci_lint.local_gate import run_gate
from ci_lint.tests.test_local_gate import TempRepoCase, _git, _repo


@dataclass(frozen=True)
class CliResult:
    code: int
    output: str
    github_output: str


class LocalReplayVerifyTest(TempRepoCase):
    def prepare(self) -> str:
        _repo(self.tmp, "print('passed')\n")
        config = self.tmp / "local-gate.toml"
        config.write_text(
            config.read_text().replace("[gate]\n", '[gate]\nmode = "enforce"\n')
            + '\n[gate.trust]\nmode = "enforce"\nskip = ["ci.yml:lint"]\naudit-rate = 0\n',
            encoding="utf-8",
        )
        _git(self.tmp, "add", "local-gate.toml")
        _git(self.tmp, "commit", "-q", "-m", "enforce")
        return _git(self.tmp, "rev-parse", "HEAD")

    def invoke(self, sha: str, act: str) -> CliResult:
        target = self.tmp / ".git" / "verify-output"
        stdout = io.StringIO()
        env = {
            "ACT": act, "GITHUB_OUTPUT": str(target), "GITHUB_ACTIONS": "true",
            "GITHUB_EVENT_PATH": "", "GITHUB_STEP_SUMMARY": "",
            "RUNNER_ENVIRONMENT": "github-hosted",
        }
        with patch.dict(os.environ, env), redirect_stdout(stdout):
            code = main([
                "local-gate", "verify", "--repo", str(self.tmp), "--sha", sha,
                "--base-sha", sha, "--event", "pull_request", "--trust",
                "--author-association", "OWNER", "--github-output",
            ])
        return CliResult(code, stdout.getvalue(), target.read_text(encoding="utf-8"))

    def test_unattested_local_replay_runs_before_it_can_create_proof(self) -> None:
        result = self.invoke(self.prepare(), "true")
        self.assertEqual(result.code, 0, result.output)
        self.assertIn("attested=false\n", result.github_output)
        self.assertIn("trusted=false\n", result.github_output)
        self.assertIn("skip_lint=false\n", result.github_output)
        self.assertIn("state=local-replay\n", result.github_output)

    def test_previous_attestation_never_skips_local_execution(self) -> None:
        self.prepare()
        self.assertEqual(run_gate(self.tmp, self.config(self.tmp)).exit_code, 0)
        result = self.invoke(_git(self.tmp, "rev-parse", "HEAD"), "true")
        self.assertEqual(result.code, 0, result.output)
        self.assertIn("attested=false\n", result.github_output)
        self.assertIn("trusted=false\n", result.github_output)
        self.assertIn("skip_lint=false\n", result.github_output)

    def test_hosted_unattested_head_still_fails_enforcement(self) -> None:
        sha = self.prepare()
        for act in ("", "false", "unknown"):
            with self.subTest(act=act):
                result = self.invoke(sha, act)
                self.assertEqual(result.code, 1, result.output)
                self.assertIn("attested=false\n", result.github_output)
