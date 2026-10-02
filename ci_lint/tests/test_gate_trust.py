"""GATE-008 -- ci_lint/gate_trust.py (zackees/ci.yml#190, pilot #189).

Each test builds a throwaway repository: a base commit on `main` carrying
the policy (`[gate.trust]`), then a PR head on top of it. The decision must
fail closed on every row except an attested, in-policy head.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ci_lint.finding import Status
from ci_lint.gate_trust import TrustInput, audit_sampled, decide
from ci_lint.local_gate import check_gate_static, load_gate_config
from ci_lint.proc import run_captured
from ci_lint.tests.helpers import requires_yaml_tooling

WORKFLOW = """name: CI
on:
  push:
    branches: [main]
  pull_request:
jobs:
  verify:
    runs-on: ubuntu-24.04
    outputs:
      trusted: ${{ steps.v.outputs.trusted }}
    steps:
      - id: v
        run: python3 -m ci_lint local-gate verify --repo . --trust --github-output
  lint:
    needs: [verify]
    if: ${{ needs.verify.outputs.trusted != 'true' }}
    runs-on: ubuntu-24.04
    steps:
      - run: PY gate.py
  tests:
    needs: [verify]
    if: ${{ needs.verify.outputs.trusted != 'true' }}
    uses: ./.github/workflows/_tests.yml
"""


def _git(repo: Path, *args: str) -> str:
    proc = run_captured(["git", "-C", str(repo), *args])
    if not proc.ok:
        raise subprocess.CalledProcessError(proc.returncode, ["git", *args], proc.stdout, proc.stderr)
    return proc.stdout.strip()


def _gate(trust: str, *, lanes: bool = False) -> str:
    py = sys.executable
    text = f'[gate]\nrun = ["{py}", "gate.py"]\nmirrors = ["ci.yml:lint"]\nverify = "ci.yml:verify"\n\n'
    if lanes:
        for lane in ("lint", "tests"):
            text += f'[gate.lanes.{lane}]\nrun = ["{py}", "gate.py", "--lane", "{lane}"]\ntools = ["git"]\n\n'
    return text + trust


TRUST = """[gate.trust]
mode = "enforce"
skip = ["ci.yml:lint", "ci.yml:tests"]
covered-by = { "ci.yml:tests" = ["tests"] }
surfaces = ["ci/remote_only/**"]
audit-rate = 0
"""


class TrustCase(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not on PATH")
        self.repo = Path(tempfile.mkdtemp())
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@example.com")
        _git(self.repo, "config", "user.name", "t")
        self.base = self.commit("base", {
            "local-gate.toml": _gate(TRUST, lanes=True),
            "gate.py": "print('ok')\n",
            "src.txt": "one\n",
            ".github/workflows/ci.yml": WORKFLOW.replace("PY", sys.executable),
            ".github/workflows/_tests.yml": "on: workflow_call\njobs:\n  t:\n    runs-on: x\n    steps:\n      - uses: ./.github/actions/prep\n",
            ".github/actions/prep/action.yml": "runs: {using: composite, steps: []}\n",
        })

    def tearDown(self) -> None:
        shutil.rmtree(self.repo, ignore_errors=True)

    def commit(self, message: str, files: dict[str, str], *, lanes: str | None = "lint:run,tests:run") -> str:
        for name, body in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", message)
        if lanes is not None:
            tree = _git(self.repo, "rev-parse", "HEAD^{tree}")
            suffix = f" lanes={lanes}" if lanes else ""
            _git(self.repo, "commit", "-q", "--amend", "-m", f"{message}\n\nLocal-Gate: v1 tree={tree} secs=1{suffix}")
        return _git(self.repo, "rev-parse", "HEAD")

    def decide(self, head: str, **overrides: object):  # type: ignore[no-untyped-def]
        fields: dict[str, object] = {
            "event": "pull_request", "head_sha": head, "base_sha": self.base, "author_association": "OWNER",
            "head_repo": "o/r", "base_repo": "o/r", "labels": (),
        }
        fields.update(overrides)
        return decide(self.repo, TrustInput(**fields))  # type: ignore[arg-type]

    def assertReason(self, head: str, reason: str, **overrides: object) -> None:
        decision = self.decide(head, **overrides)
        self.assertEqual(decision.reason, reason, decision.detail)
        self.assertEqual(decision.trusted, reason == "trusted")


class DecideTest(TrustCase):
    def test_attested_in_policy_head_is_trusted(self) -> None:
        head = self.commit("feature", {"src.txt": "two\n"})
        decision = self.decide(head)
        self.assertTrue(decision.trusted)
        self.assertIn("lanes lint, tests", decision.detail)

    def test_push_and_dispatch_always_run(self) -> None:
        head = self.commit("feature", {"src.txt": "two\n"})
        self.assertReason(head, "not-pull-request", event="push")
        self.assertReason(head, "not-pull-request", event="workflow_dispatch")

    def test_policy_comes_from_the_base_not_the_head(self) -> None:
        # The base has no [gate.trust]; a head that adds it is still untrusted.
        _git(self.repo, "checkout", "-q", "-b", "nobase", self.base)
        bare_base = self.commit("drop trust", {"local-gate.toml": _gate("", lanes=True)}, lanes=None)
        head = self.commit("re-add", {"local-gate.toml": _gate(TRUST, lanes=True)})
        self.assertReason(head, "not-opted-in", base_sha=bare_base)

    def test_label_fork_and_author_fail_closed(self) -> None:
        head = self.commit("feature", {"src.txt": "two\n"})
        self.assertReason(head, "full-label", labels=("ci-full",))
        self.assertReason(head, "fork", head_repo="someone/r")
        self.assertReason(head, "author-not-trusted", author_association="CONTRIBUTOR")
        self.assertReason(head, "author-not-trusted", author_association=None)

    def test_unattested_and_update_branch_heads_run(self) -> None:
        head = self.commit("feature", {"src.txt": "two\n"}, lanes=None)
        self.assertReason(head, "head-not-attested")
        attested = self.commit("fix", {"src.txt": "three\n"})
        _git(self.repo, "checkout", "-q", "-b", "side", self.base)
        self.commit("side", {"other.txt": "x\n"}, lanes=None)
        _git(self.repo, "checkout", "-q", "-")
        _git(self.repo, "merge", "-q", "--no-edit", "side")
        merge = _git(self.repo, "rev-parse", "HEAD")
        self.assertNotEqual(merge, attested)
        self.assertReason(merge, "head-not-attested")  # attested-parent is not enough here

    def test_trailer_must_cover_every_base_lane(self) -> None:
        head = self.commit("feature", {"src.txt": "two\n"}, lanes="lint:run")
        self.assertReason(head, "lanes-missing")
        reused = self.commit("again", {"src.txt": "three\n"}, lanes="lint:run,tests:reused@0123456789ab")
        self.assertReason(reused, "trusted")

    def test_not_applicable_counts_only_for_optional_lanes(self) -> None:
        head = self.commit("feature", {"src.txt": "two\n"}, lanes="lint:run,tests:n/a")
        self.assertReason(head, "lanes-missing")  # tests is required on the base
        _git(self.repo, "checkout", "-q", "-b", "opt", self.base)
        gate = _gate(TRUST, lanes=True).replace('"--lane", "tests"]\ntools = ["git"]\n', '"--lane", "tests"]\ntools = ["git"]\noptional = true\n')
        base = self.commit("tests optional", {"local-gate.toml": gate}, lanes=None)
        head = self.commit("feature", {"src.txt": "three\n"}, lanes="lint:run,tests:n/a")
        self.assertReason(head, "trusted", base_sha=base)

    def test_gate_surfaces_force_a_remote_run(self) -> None:
        for path in (
            "local-gate.toml",  # the declaration
            "gate.py",  # named in the gate's argv
            ".github/workflows/ci.yml",  # defines the skipped jobs
            ".github/workflows/_tests.yml",  # reusable workflow a skip job calls
            ".github/actions/prep/action.yml",  # action that workflow uses
            "ci/remote_only/check.py",  # declared surface
        ):
            with self.subTest(path=path):
                _git(self.repo, "checkout", "-q", "-B", "pr", self.base)
                body = (self.repo / path).read_text(encoding="utf-8") + "\n# edit\n" if (self.repo / path).exists() else "x\n"
                head = self.commit("touch", {path: body})
                self.assertReason(head, "surface-changed")
        # An unrelated workflow is not a surface of the skipped jobs.
        _git(self.repo, "checkout", "-q", "-B", "pr", self.base)
        head = self.commit("release wf", {".github/workflows/release.yml": "on: workflow_dispatch\n"})
        self.assertReason(head, "trusted")

    def test_missing_base_history_fails_closed(self) -> None:
        head = self.commit("feature", {"src.txt": "two\n"})
        self.assertReason(head, "base-unavailable", base_sha=None)
        self.assertReason(head, "base-unavailable", base_sha="f" * 40)

    def test_shadow_reports_without_skipping(self) -> None:
        shadow = TRUST.replace('mode = "enforce"', 'mode = "shadow"')
        _git(self.repo, "checkout", "-q", "-b", "shadow", self.base)
        base = self.commit("shadow base", {"local-gate.toml": _gate(shadow, lanes=True)}, lanes=None)
        head = self.commit("feature", {"src.txt": "two\n"})
        decision = self.decide(head, base_sha=base)
        self.assertEqual((decision.trusted, decision.would_trust, decision.reason), (False, True, "shadow"))

    def test_audit_sample_is_deterministic_and_roughly_one_in_n(self) -> None:
        shas = [f"{i:040x}" for i in range(2000)]
        hits = sum(audit_sampled(s, 10) for s in shas)
        self.assertTrue(150 < hits < 250, hits)
        self.assertEqual([audit_sampled(s, 10) for s in shas[:50]], [audit_sampled(s, 10) for s in shas[:50]])
        self.assertFalse(any(audit_sampled(s, 0) for s in shas[:50]))


class AttestedJobsTest(TrustCase):
    """GATE-010: per-job skip from the base's ci-attestations.yml and the head's trailers."""

    def test_job_skips_only_when_all_its_gates_are_attested(self) -> None:
        from ci_lint.attestations import make
        from ci_lint.local_gate_cli import _job_decisions

        definition = (
            "version: 1\ngates:\n  rust/all/lint: {lane: lint}\n  rust/x86_64-unknown-linux-gnu/test: {lane: tests}\n"
            "jobs:\n  ci.yml:lint: [rust/all/lint]\n  ci.yml:tests: [rust/all/lint, rust/x86_64-unknown-linux-gnu/test]\n"
        )
        base = self.commit("definition", {"ci-attestations.yml": definition}, lanes=None)
        self.commit("feature", {"src.txt": "two\n"})
        tree = _git(self.repo, "rev-parse", "HEAD^{tree}")
        parents = (base,)
        lint = make("rust/all/lint", tree=tree, parents=parents, lane="lint", key="k", via="run", secs=1).trailer()
        message = _git(self.repo, "log", "-1", "--format=%B")
        _git(self.repo, "commit", "-q", "--amend", "-m", message + "\n" + lint)
        head = _git(self.repo, "rev-parse", "HEAD")
        decision = self.decide(head, base_sha=base)
        self.assertTrue(decision.trusted, decision.detail)
        inp = TrustInput("pull_request", head, base, "OWNER", "o/r", "o/r", ())
        jobs = {j.job: j.skip for j in _job_decisions(self.repo, inp, decision.trusted)}
        self.assertEqual(jobs, {"ci.yml:lint": True, "ci.yml:tests": False})  # test gate omitted = not run
        untrusted = {j.job: j.skip for j in _job_decisions(self.repo, inp, False)}
        self.assertEqual(untrusted, {"ci.yml:lint": False, "ci.yml:tests": False})

    def test_definition_change_is_a_surface(self) -> None:
        head = self.commit("touch definition", {"ci-attestations.yml": "version: 1\ngates:\n  rust/all/x: {lane: lint}\n"})
        self.assertReason(head, "surface-changed")


@requires_yaml_tooling
class StaticTest(TrustCase):
    def findings(self) -> list[str]:
        loaded = load_gate_config(self.repo)
        assert loaded.config is not None
        return [f"{f.rule}:{f.status.value}:{f.message}" for f in check_gate_static(loaded.config, self.repo)
                if f.rule == "GATE-008"]

    def test_wired_declaration_is_clean(self) -> None:
        self.assertEqual(self.findings(), [])

    def test_skip_job_without_trust_condition(self) -> None:
        wf = self.repo / ".github/workflows/ci.yml"
        wf.write_text(wf.read_text(encoding="utf-8").replace(
            "  lint:\n    needs: [verify]\n    if: ${{ needs.verify.outputs.trusted != 'true' }}\n", "  lint:\n    needs: [verify]\n"),
            encoding="utf-8")
        self.assertTrue(any("'lint' has no job-level if:" in f for f in self.findings()), self.findings())

    def test_non_mirror_skip_needs_covering_lanes(self) -> None:
        gate = self.repo / "local-gate.toml"
        gate.write_text(gate.read_text(encoding="utf-8").replace('covered-by = { "ci.yml:tests" = ["tests"] }\n', ""),
                        encoding="utf-8")
        self.assertTrue(any("names no covering lanes" in f for f in self.findings()), self.findings())
        gate.write_text(gate.read_text(encoding="utf-8").replace("[gate.trust]\n", '[gate.trust]\ncovered-by = { "ci.yml:tests" = ["nope"] }\n'),
                        encoding="utf-8")
        self.assertTrue(any("undeclared lane(s) nope" in f for f in self.findings()), self.findings())

    def test_workflow_without_push_trigger_has_no_post_merge_catch(self) -> None:
        wf = self.repo / ".github/workflows/ci.yml"
        wf.write_text(wf.read_text(encoding="utf-8").replace("  push:\n    branches: [main]\n", ""), encoding="utf-8")
        self.assertTrue(any(f"GATE-008:{Status.VIOLATION.value}:ci.yml has no push trigger" in f for f in self.findings()),
                        self.findings())

    def test_bad_mode_and_audit_rate(self) -> None:
        gate = self.repo / "local-gate.toml"
        gate.write_text(gate.read_text(encoding="utf-8").replace('mode = "enforce"', 'mode = "always"').replace(
            "audit-rate = 0", "audit-rate = 1"), encoding="utf-8")
        rules = [f.message for f in load_gate_config(self.repo).findings if f.rule == "GATE-008"]
        self.assertEqual(len(rules), 2, rules)


if __name__ == "__main__":
    unittest.main()
