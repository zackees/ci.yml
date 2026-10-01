"""GATE-001..004 -- ci_lint/local_gate.py, ci_lint/first_pass.py (zackees/ci.yml#166).

Every test that needs git builds a throwaway repository in a tempdir; the
static-rule tests also need YAML tooling and are skipped without it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ci_lint.cargo_messages import JsonValue
from ci_lint.finding import Status
from ci_lint.first_pass import RunSample, classify, collect
from ci_lint.local_gate import (
    HOOK_MARKER,
    Attestation,
    check_commit,
    check_gate_static,
    check_push,
    install_hook,
    load_gate_config,
    parse_attestation,
    run_gate,
    verify,
)
from ci_lint.schema import load_ci_toml
from ci_lint.tests.helpers import requires_yaml_tooling

TREE = "a" * 40
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "rust-pypi-app" / "ci.toml"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _repo(tmp: Path, gate_body: str) -> Path:
    """A git repo whose gate is `python3 gate.py`; `gate.py` runs `gate_body`."""

    _git(tmp, "init", "-q", "-b", "main")
    _git(tmp, "config", "user.email", "t@example.com")
    _git(tmp, "config", "user.name", "t")
    (tmp / "local-gate.toml").write_text(
        f'[gate]\nrun = ["{sys.executable}", "gate.py"]\nmirrors = ["ci.yml:lint"]\nverify = "ci.yml:verify"\n',
        encoding="utf-8",
    )
    (tmp / "gate.py").write_text(gate_body, encoding="utf-8")
    (tmp / "src.txt").write_text("one\n", encoding="utf-8")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-q", "-m", "init")
    return tmp


class TempRepoCase(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not on PATH")
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def config(self, repo: Path):  # type: ignore[no-untyped-def]
        config, findings = load_gate_config(repo)
        self.assertEqual(findings, [])
        assert config is not None
        return config


class AttestationTest(unittest.TestCase):
    def test_round_trip_and_last_trailer_wins(self) -> None:
        att = Attestation(tree=TREE, secs=12)
        msg = f"subject\n\nbody\n\nLocal-Gate: v1 tree={'b' * 40}\n{att.trailer()}\n"
        self.assertEqual(parse_attestation(msg), att)

    def test_rejects_unknown_version_and_bad_tree(self) -> None:
        self.assertIsNone(parse_attestation(f"s\n\nLocal-Gate: v9 tree={TREE}\n"))
        self.assertIsNone(parse_attestation("s\n\nLocal-Gate: v1 tree=abc\n"))
        self.assertIsNone(parse_attestation("no trailer"))


class ConfigTest(TempRepoCase):
    def test_absent_means_not_opted_in(self) -> None:
        self.assertEqual(load_gate_config(self.tmp), (None, []))

    def test_unknown_key_and_bad_mirror(self) -> None:
        (self.tmp / "local-gate.toml").write_text(
            '[gate]\nrun = ["x"]\nmirrors = ["lint"]\nbogus = 1\n', encoding="utf-8"
        )
        _config, findings = load_gate_config(self.tmp)
        self.assertEqual({f.rule for f in findings}, {"CT-001", "GATE-001"})

    def test_ci_toml_local_gate_parses_through_schema(self) -> None:
        text = EXAMPLE.read_text(encoding="utf-8")
        text += '\n[local.gate]\nrun = ["python3", "ci/gate.py"]\nmirrors = ["ci.yml:fast"]\nverify = "ci.yml:precheck"\n'
        (self.tmp / "ci.toml").write_text(text, encoding="utf-8")
        ci, findings = load_ci_toml(self.tmp)
        self.assertFalse([f for f in findings if "local" in f.message or f.rule == "GATE-001"], findings)
        assert ci is not None and ci.local.gate is not None
        self.assertEqual(ci.local.gate.run, ("python3", "ci/gate.py"))
        config, _ = load_gate_config(self.tmp)
        assert config is not None
        self.assertEqual(str(config.verify), "ci.yml:precheck")

    def test_both_declarations_is_gate_001(self) -> None:
        text = EXAMPLE.read_text(encoding="utf-8") + '\n[local.gate]\nrun = ["a"]\n'
        (self.tmp / "ci.toml").write_text(text, encoding="utf-8")
        (self.tmp / "local-gate.toml").write_text('[gate]\nrun = ["b"]\n', encoding="utf-8")
        config, findings = load_gate_config(self.tmp)
        self.assertIn("GATE-001", {f.rule for f in findings})
        assert config is not None
        self.assertEqual(config.run, ("a",))


class RunGateTest(TempRepoCase):
    def test_pass_stamps_head_and_second_run_is_a_no_op(self) -> None:
        repo = _repo(self.tmp, "print('ok')\n")
        before_tree = _git(repo, "rev-parse", "HEAD^{tree}")
        outcome = run_gate(repo, self.config(repo))
        self.assertEqual(outcome.exit_code, 0, outcome.message)
        self.assertEqual(_git(repo, "rev-parse", "HEAD^{tree}"), before_tree)
        self.assertEqual(check_commit(repo, "HEAD").state, "attested")
        again = run_gate(repo, self.config(repo))
        self.assertIn("already attested", again.message)

    def test_failing_gate_does_not_stamp(self) -> None:
        repo = _repo(self.tmp, "raise SystemExit(3)\n")
        outcome = run_gate(repo, self.config(repo))
        self.assertEqual(outcome.exit_code, 3)
        self.assertEqual(check_commit(repo, "HEAD").state, "missing")

    def test_dirty_tree_is_refused(self) -> None:
        repo = _repo(self.tmp, "print('ok')\n")
        (repo / "src.txt").write_text("two\n", encoding="utf-8")
        self.assertEqual(run_gate(repo, self.config(repo)).exit_code, 2)

    def test_gate_that_rewrites_files_is_refused(self) -> None:
        repo = _repo(self.tmp, "open('src.txt','w').write('formatted\\n')\n")
        outcome = run_gate(repo, self.config(repo))
        self.assertEqual(outcome.exit_code, 1)
        self.assertIn("changed the repository", outcome.message)
        self.assertEqual(check_commit(repo, "HEAD").state, "missing")

    def test_content_amend_after_gate_is_stale(self) -> None:
        repo = _repo(self.tmp, "print('ok')\n")
        run_gate(repo, self.config(repo))
        (repo / "src.txt").write_text("sneaky\n", encoding="utf-8")
        _git(repo, "commit", "-q", "-a", "--amend", "--no-edit")
        self.assertEqual(check_commit(repo, "HEAD").state, "stale")

    def test_merge_from_base_on_attested_parent(self) -> None:
        repo = _repo(self.tmp, "print('ok')\n")
        _git(repo, "checkout", "-q", "-b", "feature")
        (repo / "f.txt").write_text("f\n", encoding="utf-8")
        _git(repo, "add", "f.txt")
        _git(repo, "commit", "-q", "-m", "feature")
        run_gate(repo, self.config(repo))
        _git(repo, "checkout", "-q", "main")
        (repo / "m.txt").write_text("m\n", encoding="utf-8")
        _git(repo, "add", "m.txt")
        _git(repo, "commit", "-q", "-m", "main moved")
        _git(repo, "checkout", "-q", "feature")
        _git(repo, "merge", "-q", "--no-edit", "main")
        self.assertEqual(check_commit(repo, "HEAD").state, "attested-parent")


class VerifyAndHookTest(TempRepoCase):
    def test_verify_outcomes(self) -> None:
        repo = _repo(self.tmp, "print('ok')\n")
        config = self.config(repo)
        head = _git(repo, "rev-parse", "HEAD")
        self.assertEqual(verify(repo, config, sha=head, event="push", author=None).state, "not-applicable")
        self.assertEqual(
            verify(repo, config, sha=head, event="pull_request", author="dependabot[bot]").state, "exempt"
        )
        self.assertEqual(verify(repo, config, sha=head, event="pull_request", author="dev").exit_code, 1)
        shadow = config.__class__(**{**config.__dict__, "mode": "shadow"})
        self.assertEqual(verify(repo, shadow, sha=head, event="pull_request", author="dev").exit_code, 0)
        run_gate(repo, config)
        head = _git(repo, "rev-parse", "HEAD")
        self.assertTrue(verify(repo, config, sha=head, event="pull_request", author="dev").attested)

    def test_check_push(self) -> None:
        repo = _repo(self.tmp, "print('ok')\n")
        head = _git(repo, "rev-parse", "HEAD")
        line = f"refs/heads/main {head} refs/heads/main {'0' * 40}\n"
        self.assertEqual(check_push(repo, line)[0], 1)
        tag = f"refs/tags/v1 {head} refs/tags/v1 {'0' * 40}\n"
        self.assertEqual(check_push(repo, tag)[0], 0)
        run_gate(repo, self.config(repo))
        head = _git(repo, "rev-parse", "HEAD")
        self.assertEqual(check_push(repo, f"refs/heads/main {head} refs/heads/main {'0' * 40}\n")[0], 0)

    def test_install_hook_refuses_foreign_hook(self) -> None:
        repo = _repo(self.tmp, "print('ok')\n")
        code, _ = install_hook(repo, "ci-lint")
        self.assertEqual(code, 0)
        hook = repo / ".git" / "hooks" / "pre-push"
        self.assertIn(HOOK_MARKER, hook.read_text(encoding="utf-8"))
        hook.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        self.assertEqual(install_hook(repo, "ci-lint")[0], 1)
        self.assertEqual(install_hook(repo, "ci-lint", force=True)[0], 0)


WORKFLOW_GREEN = """\
name: CI
on: [pull_request]
jobs:
  verify:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
      - run: python3 -m ci_lint local-gate verify --repo .
  lint:
    needs: verify
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
      - name: Enlarge swap
        run: sudo fallocate -l 4G /swap
      - run: |
          set -euo pipefail
          GATE_LANE=remote PYGATE --lane lint
  build:
    needs: lint
    runs-on: ubuntu-24.04
    steps:
      - run: cargo build
"""


@requires_yaml_tooling
class StaticRulesTest(TempRepoCase):
    def _write(self, workflow: str, *, setup_steps: str = '["Enlarge swap"]') -> Path:
        wf_dir = self.tmp / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(workflow.replace("PYGATE", "python3 ci/gate.py"), encoding="utf-8")
        (self.tmp / "local-gate.toml").write_text(
            '[gate]\nrun = ["python3", "ci/gate.py"]\nmirrors = ["ci.yml:lint"]\nverify = "ci.yml:verify"\n'
            f"setup-steps = {setup_steps}\n",
            encoding="utf-8",
        )
        return self.tmp

    def _rules(self, repo: Path) -> list[str]:
        return [f.rule for f in check_gate_static(self.config(repo), repo) if f.status == Status.VIOLATION]

    def test_repo_without_workflows_needs_no_mirror_or_verify(self) -> None:
        (self.tmp / "local-gate.toml").write_text('[gate]\nrun = ["python3", "ci/gate.py"]\n', encoding="utf-8")
        self.assertEqual(check_gate_static(self.config(self.tmp), self.tmp), [])

    def test_green(self) -> None:
        self.assertEqual(self._rules(self._write(WORKFLOW_GREEN)), [])

    def test_remote_only_command_in_mirror_is_gate_001(self) -> None:
        wf = WORKFLOW_GREEN.replace("--lane lint\n", "--lane lint\n          cargo clippy\n")
        self.assertEqual(self._rules(self._write(wf)), ["GATE-001"])

    def test_unlisted_setup_run_step_is_gate_001(self) -> None:
        self.assertEqual(self._rules(self._write(WORKFLOW_GREEN, setup_steps="[]")), ["GATE-001"])

    def test_remote_only_action_in_mirror_is_gate_001(self) -> None:
        wf = WORKFLOW_GREEN.replace(
            "      - name: Enlarge swap", "      - uses: ./.github/actions/extra-lint\n      - name: Enlarge swap"
        )
        self.assertEqual(self._rules(self._write(wf)), ["GATE-001"])

    def test_job_not_behind_verify_is_gate_002(self) -> None:
        wf = WORKFLOW_GREEN + "  docs:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo hi\n"
        self.assertEqual(self._rules(self._write(wf)), ["GATE-002"])

    def test_verify_job_without_verify_step_is_gate_002(self) -> None:
        wf = WORKFLOW_GREEN.replace("python3 -m ci_lint local-gate verify --repo .", "echo skipped")
        self.assertEqual(self._rules(self._write(wf)), ["GATE-002"])


class FirstPassTest(unittest.TestCase):
    def test_classify(self) -> None:
        one = [RunSample("s1", "success", 1, "2026-10-01T00:00:00Z", True),
               RunSample("s1", "cancelled", 1, "2026-10-01T00:00:01Z", True)]
        self.assertEqual(classify(one), (1, 0, 0, True, True))
        fixup = one + [RunSample("s2", "success", 1, "2026-10-01T01:00:00Z", False)]
        self.assertFalse(classify(fixup)[3])
        rerun = [RunSample("s1", "success", 2, "2026-10-01T00:00:00Z", False)]
        self.assertFalse(classify(rerun)[3])

    def test_collect_with_recorded_responses(self) -> None:
        pulls: JsonValue = [
            {"number": 7, "title": "t", "head": {"ref": "b7"}, "created_at": "2026-10-01T00:00:00Z",
             "merged_at": "2026-10-01T02:00:00Z", "updated_at": "2026-10-01T02:00:00Z"},
        ]
        runs: JsonValue = {
            "workflow_runs": [
                {"path": ".github/workflows/ci.yml", "head_sha": "s1", "conclusion": "success", "run_attempt": 1,
                 "created_at": "2026-10-01T00:01:00Z",
                 "head_commit": {"message": f"x\n\nLocal-Gate: v1 tree={TREE}"}},
                {"path": ".github/workflows/other.yml", "head_sha": "s0", "conclusion": "failure", "run_attempt": 1,
                 "created_at": "2026-10-01T00:01:00Z", "head_commit": {"message": "x"}},
            ]
        }

        def fetch(url: str, _token: str) -> JsonValue:
            return pulls if "/pulls?" in url else runs

        report = collect("o/r", "ci.yml", datetime(2026, 9, 30, tzinfo=timezone.utc), fetch, "tok", min_prs=1)
        self.assertEqual(report.rate, 1.0)
        self.assertTrue(report.prs[0].attested)
        self.assertIsNone(report.finding())


if __name__ == "__main__":
    unittest.main()
