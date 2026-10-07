"""Qualified publication checks the outgoing commit's full portable proof."""

import io
import time
from contextlib import redirect_stderr
from unittest.mock import patch

from ci_lint.attestations import make
from ci_lint.cli import main
from ci_lint.local_gate import Attestation, check_push, stamp_head
from ci_lint.tests.test_local_gate import TempRepoCase, _git, _repo


class AttestationPushTest(TempRepoCase):
    def prepare(self, *, attest: bool = True, age: int = 0) -> str:
        _repo(self.tmp, "print('unused fixture command')\n")
        (self.tmp / "local-gate.toml").write_text('''
[gate]
run = ["bosn", "ci", "run", "--wait", "--json"]
[gate.lanes.rust]
run = ["bosn", "ci", "run", "--wait", "--json"]
tools = ["bosn"]
max-age-hours = 1
[gate.lanes.cross]
run = ["bosn", "ci", "run", "--wait", "--json"]
tools = ["bosn"]
optional = true
[gate.replay]
repository = "zackees/tool"
workflow = "ci.yml"
mode = "minimal"
qualified = true
report-source = "stdout"
provider-query = ["bosn", "ci", "runners", "list", "--json"]
[[gate.replay.jobs]]
source-job = "ci.yml:rust"
lanes = ["rust", "cross"]
''')
        (self.tmp / "ci-attestations.yml").write_text('''
version: 1
gates:
  rust/all/fmt: {lane: rust}
  rust/x86_64-pc-windows-msvc/test: {lane: cross}
''')
        _git(self.tmp, "add", "-A")
        _git(self.tmp, "commit", "-q", "-m", "enroll")
        tree = _git(self.tmp, "rev-parse", "HEAD^{tree}")
        parents = tuple(_git(self.tmp, "log", "-1", "--format=%P").split())
        record = make("rust/all/fmt", tree=tree, parents=parents, lane="rust", key="fixture",
                      via="run", secs=1, at=int(time.time()) - age)
        return stamp_head(self.tmp, Attestation(tree=tree, secs=1), (record.trailer(),) if attest else ())

    def push(self, sha: str) -> int:
        return check_push(self.tmp, f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n").exit_code

    def test_local_gate_alone_cannot_publish_enrolled_head(self) -> None:
        self.assertEqual(self.push(self.prepare(attest=False)), 1)

    def test_expired_per_gate_proof_cannot_publish(self) -> None:
        self.assertEqual(self.push(self.prepare(age=3601)), 1)

    def test_fresh_required_proof_allows_omitted_optional_gate(self) -> None:
        self.assertEqual(self.push(self.prepare()), 0)

    def test_exact_outgoing_policy_ignores_worktree_changes(self) -> None:
        sha = self.prepare()
        (self.tmp / "ci-attestations.yml").write_text("broken worktree file\n")
        (self.tmp / "local-gate.toml").write_text("broken worktree policy\n")
        self.assertEqual(self.push(sha), 0)

    def test_malformed_transport_cannot_publish(self) -> None:
        sha = self.prepare()
        message = _git(self.tmp, "log", "-1", "--format=%B", sha)
        _git(self.tmp, "commit", "-q", "--amend", "-m", message + '\nCi-Attestation: {broken}\n')
        self.assertEqual(self.push(_git(self.tmp, "rev-parse", "HEAD")), 1)

    def test_duplicate_valid_record_cannot_publish(self) -> None:
        sha = self.prepare()
        message = _git(self.tmp, "log", "-1", "--format=%B", sha)
        record = next(line for line in message.splitlines() if line.startswith("Ci-Attestation:"))
        _git(self.tmp, "commit", "-q", "--amend", "-m", message + '\n' + record + '\n')
        self.assertEqual(self.push(_git(self.tmp, "rev-parse", "HEAD")), 1)

    def test_hook_cli_cannot_bypass_outgoing_policy_with_broken_worktree(self) -> None:
        sha = self.prepare(attest=False)
        (self.tmp / "local-gate.toml").write_text("not valid TOML\n")
        line = f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n"
        with patch("sys.stdin", io.StringIO(line)), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["local-gate", "check-push", "--repo", str(self.tmp)]), 1)

    def test_outgoing_without_gate_policy_retains_no_hook_requirement(self) -> None:
        self.prepare()
        _git(self.tmp, "mv", "local-gate.toml", "archived-gate.toml")
        _git(self.tmp, "commit", "-q", "-m", "unenrolled head")
        self.assertEqual(self.push(_git(self.tmp, "rev-parse", "HEAD")), 0)

    def test_enrolled_publication_without_declared_lanes_is_explicitly_rejected(self) -> None:
        self.prepare()
        path = self.tmp / "local-gate.toml"
        text = path.read_text()
        path.write_text(text.split("[gate.lanes.rust]")[0] + "[gate.replay]"
                        + text.split("[gate.replay]")[1].replace('lanes = ["rust", "cross"]', ''))
        _git(self.tmp, "add", "local-gate.toml")
        _git(self.tmp, "commit", "-q", "-m", "no lane enrollment")
        sha = stamp_head(self.tmp, Attestation(tree=_git(self.tmp, "rev-parse", "HEAD^{tree}"), secs=1))
        result = check_push(self.tmp, f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("requires declared gate lanes", result.problems[0])
