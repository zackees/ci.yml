"""Merged evidence is rebound only after an exact-tree trusted PR proof."""
from __future__ import annotations

import unittest
import argparse
import os
from unittest.mock import patch
from pathlib import Path
from dataclasses import replace

from ci_lint.attestations import make, parse_definition, CommitAttestations, GateStatus, DefinitionLoad
from ci_lint.gate_trust import TrustDecision
from ci_lint.attestation_promotion import PromotionInput, promote


class PromotionTest(unittest.TestCase):
    def test_exact_tree_rebinds_parents_and_preserves_freshness(self) -> None:
        definition = parse_definition("version: 1\ngates:\n  rust/all/fmt: {lane: rust}\njobs:\n  ci.yml:lint: [rust/all/fmt]\n").definition
        assert definition is not None
        att = replace(make("rust/all/fmt", tree="t", parents=("old",), lane="rust", key="k", via="run", secs=3), at=100).stamped()
        inp = PromotionInput("zackees/soldr", "merged", "push", "refs/heads/main", "main", 200)
        pr = {"number": 1, "merged_at": "2026-10-03T00:00:00Z", "merge_commit_sha": "merged", "author_association": "OWNER", "base": {"ref": "main", "sha": "base", "repo": {"full_name": "zackees/soldr"}}, "head": {"sha": "head", "repo": {"full_name": "zackees/soldr"}}, "labels": []}
        shown = {("rev-parse", "head^{tree}"): "t", ("rev-parse", "merged^{tree}"): "t", ("log", "-1", "--format=%P", "merged"): "new", ("log", "-1", "--format=%B", "head"): att.trailer()}
        with patch("ci_lint.attestation_promotion._git", side_effect=lambda repo, *args: shown.get(args, "")), patch("ci_lint.attestation_promotion.load_definition") as load, patch("ci_lint.attestation_promotion.decide_trust", return_value=TrustDecision(True, True, "trusted", "ok")), patch("ci_lint.attestation_promotion.verify_commit", return_value=CommitAttestations("head", (GateStatus(att.gate, "valid", "ok"),), ())):
            load.return_value = DefinitionLoad(definition, [])
            result = promote(Path("."), inp, lambda url, token: [pr], "token")
            self.assertEqual(result.records[0].parents, ("new",))
            self.assertEqual(result.records[0].at, 100)
            self.assertEqual(result.records[0].compute_stamp(), result.records[0].stamp)
            self.assertEqual(promote(Path("."), replace(inp, event="release"), lambda url, token: self.fail("release contacted API"), "token").reason, "not-default-push")
            shown[("log", "-1", "--format=%P", "merged")] = ""
            self.assertEqual(promote(Path("."), inp, lambda url, token: [pr], "token").reason, "missing-main-parents")
            shown[("log", "-1", "--format=%P", "merged")] = "new"
            self.assertEqual(promote(Path("."), replace(inp, now=100000), lambda url, token: [pr], "token").records, ())
            self.assertEqual(promote(Path("."), inp, lambda url, token: [pr, pr], "token").reason, "ambiguous-pr")
            self.assertEqual(promote(Path("."), replace(inp, ref="refs/tags/v1"), lambda url, token: self.fail("tag contacted API"), "token").reason, "not-default-push")
            shown[("rev-parse", "merged^{tree}")] = "different"
            self.assertEqual(promote(Path("."), inp, lambda url, token: [pr], "token").reason, "tree-mismatch")
            shown[("rev-parse", "merged^{tree}")] = "t"
            load.side_effect = [DefinitionLoad(definition, []), None]
            self.assertEqual(promote(Path("."), inp, lambda url, token: [pr], "token").reason, "definition-changed")


class PublicationTest(unittest.TestCase):
    def test_promoted_record_is_published_under_main_lineage(self) -> None:
        from ci_lint.attest_cli import _cmd_keys
        from ci_lint.attestation_promotion import Promotion
        from ci_lint.cache_lineage import Lineage

        definition = parse_definition("version: 1\ngates:\n  rust/all/fmt: {lane: rust}\njobs:\n  ci.yml:lint: [rust/all/fmt]\n").definition
        assert definition is not None
        record = make("rust/all/fmt", tree="tree", parents=("main-parent",), lane="rust", key="key", via="run", secs=3)
        args = argparse.Namespace(repo=".", base=None, commit="HEAD", main_ref="origin/main", pr=None,
                                  out_dir="/tmp/promoted-records", slots=8, github_output=True, promote_merged_pr=True,
                                  github_repo="zackees/soldr", promotion_max_age_hours=24, fetch_promotion_source=False)
        with patch("ci_lint.attest_cli._definition", return_value=definition), patch("ci_lint.attest_cli._lineage", return_value=Lineage(5, "a" * 40)), patch("ci_lint.attest_cli.verify_commit", return_value=CommitAttestations("a" * 40, (), ())), patch("ci_lint.attestation_promotion.promote", return_value=Promotion((record,), "promoted", "source", 1)), patch("ci_lint.attest_cli._write_outputs") as outputs, patch.object(Path, "mkdir"), patch.object(Path, "write_text") as write, patch.dict(os.environ, {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main"}):
            self.assertEqual(_cmd_keys(args), 0)
        self.assertIn("key_0=att1-rust.all.fmt-m5-aaaaaaaaaa", outputs.call_args.args[0])
        self.assertEqual(write.call_args.args[0].strip(), record.compact())


class GitPromotionTest(unittest.TestCase):
    def test_real_squash_proof_and_ancestor_resolution(self) -> None:
        import tempfile
        import shutil
        import time
        from ci_lint.tests.test_gate_trust import _git, _gate, TRUST
        from ci_lint.cache_lineage import lineage_of, cache_key, resolve

        repo = Path(tempfile.mkdtemp())
        try:
            _git(repo, "init", "-q", "-b", "main")
            _git(repo, "config", "user.name", "test")
            _git(repo, "config", "user.email", "test@example.com")
            (repo / "local-gate.toml").write_text(_gate(TRUST))
            (repo / "ci-attestations.yml").write_text("version: 1\ngates:\n  rust/all/fmt: {lane: rust}\njobs:\n  ci.yml:lint: [rust/all/fmt]\n")
            (repo / "gate.py").write_text("print('ok')\n")
            _git(repo, "add", ".")
            _git(repo, "commit", "-qm", "base")
            base = _git(repo, "rev-parse", "HEAD")
            _git(repo, "checkout", "-qb", "pr")
            (repo / "source.rs").write_text("fn main() {}\n")
            _git(repo, "add", ".")
            _git(repo, "commit", "-qm", "feature")
            tree = _git(repo, "rev-parse", "HEAD^{tree}")
            att = make("rust/all/fmt", tree=tree, parents=(base,), lane="rust", key="k", via="run", secs=3)
            _git(repo, "commit", "-q", "--amend", "-m", f"feature\n\nLocal-Gate: v1 tree={tree} secs=3\n{att.trailer()}")
            source = _git(repo, "rev-parse", "HEAD")
            # Insert an empty main commit so squash parents differ while tree stays identical.
            _git(repo, "checkout", "-q", "main")
            _git(repo, "commit", "-q", "--allow-empty", "-m", "main advance")
            _git(repo, "merge", "--squash", "pr")
            _git(repo, "commit", "-qm", "squash")
            merged = _git(repo, "rev-parse", "HEAD")
            pr = {"number": 1, "merged_at": "2026-10-03T00:00:00Z", "merge_commit_sha": merged, "author_association": "OWNER", "base": {"ref": "main", "sha": base, "repo": {"full_name": "zackees/soldr"}}, "head": {"sha": source, "repo": {"full_name": "zackees/soldr"}}, "labels": []}
            inp = PromotionInput("zackees/soldr", merged, "push", "refs/heads/main", "main", int(time.time()))
            result = promote(repo, inp, lambda url, token: [pr], "token")
            self.assertEqual(result.reason, "promoted")
            self.assertEqual(len(result.records), 1)
            self.assertNotEqual(result.records[0].parents, att.parents)
            self.assertEqual(result.records[0].tree, tree)
            lineage = lineage_of(repo, merged, main_ref="main")
            payload = cache_key("dylint", lineage)
            selected = resolve(repo, lineage, [payload], family="dylint", required_shas=frozenset({lineage.sha[:10]}))
            self.assertEqual(selected.key, payload)
            _git(repo, "checkout", "-q", "pr")
            message = _git(repo, "log", "-1", "--format=%B")
            _git(repo, "commit", "-q", "--amend", "-m", message + "\n" + att.trailer())
            duplicate = {**pr, "head": {"sha": _git(repo, "rev-parse", "HEAD"), "repo": {"full_name": "zackees/soldr"}}}
            self.assertEqual(promote(repo, inp, lambda url, token: [duplicate], "token").reason, "invalid-source")
            for invalid in ({**pr, "author_association": []}, {**pr, "labels": [{}]}):
                self.assertEqual(promote(repo, inp, lambda url, token: [invalid], "token").reason, "malformed-pr")
        finally:
            shutil.rmtree(repo)


class FetchTest(unittest.TestCase):
    def test_bounded_fetch_verifies_exact_head_and_keeps_token_out_of_argv(self) -> None:
        from ci_lint.attestation_promotion import _ensure_source
        from ci_lint.proc import Captured

        source = "a" * 40
        pr = {"number": 1, "author_association": "OWNER", "head": {"sha": source, "repo": {"full_name": "zackees/soldr"}}, "base": {"ref": "main", "sha": "b" * 40}}
        inp = PromotionInput("zackees/soldr", "c" * 40, "push", "refs/heads/main", "main", 200, fetch_missing=True)
        with patch("ci_lint.attestation_promotion._git", side_effect=["", source]), patch("ci_lint.attestation_promotion.run_captured", return_value=Captured(0, "", "")) as fetch:
            self.assertTrue(_ensure_source(Path("."), inp, pr, "secret"))
            self.assertEqual(fetch.call_args.kwargs["timeout"], 60)
            self.assertNotIn("secret", " ".join(fetch.call_args.args[0]))
        with patch("ci_lint.attestation_promotion._git", side_effect=["", "d" * 40]), patch("ci_lint.attestation_promotion.run_captured", return_value=Captured(0, "", "")):
            self.assertFalse(_ensure_source(Path("."), inp, pr, "secret"))
        with patch("ci_lint.attestation_promotion.run_captured") as fetch:
            self.assertFalse(_ensure_source(Path("."), inp, {**pr, "number": True}, "secret"))
            self.assertFalse(_ensure_source(Path("."), inp, {**pr, "head": {"sha": "--bad", "repo": {"full_name": inp.slug}}}, "secret"))
            fetch.assert_not_called()
