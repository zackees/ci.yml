"""GATE-010 -- ci_lint/attestations.py, mini_yaml.py, cache_lineage.py (zackees/ci.yml#198).

Owner decisions under test: entries must name declared gates; omission means
not run; a job is skipped only when every gate it lists is attested; the
stamp binds the record to its tree and parents; cache keys are
human-readable and ancestor-defining.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ci_lint.attestations import (
    MISSING,
    VALID,
    decide_jobs,
    make,
    parse_definition,
    parse_trailers,
    validate_gate_path,
    verify_commit,
)
from ci_lint.cache_lineage import Lineage, lineage_of, parse_key, resolve
from ci_lint.local_gate import load_gate_config, run_gate
from ci_lint.mini_yaml import MiniYamlError, parse
from ci_lint.proc import run_captured

DEFINITION = """\
# soldr-shaped pilot definition (comments are allowed)
version: 1
gates:
  rust/all/fmt: {lane: rust}
  rust/x86_64-unknown-linux-gnu/clippy: {lane: rust}
  rust/x86_64-unknown-linux-gnu/test:
    lane: tests          # nextest + doctests in bosn
  rust/x86_64-pc-windows-msvc/clippy: {lane: cross}   # declared, not yet produced
jobs:
  ci.yml:build-linux-x64: [rust/all/fmt, rust/x86_64-unknown-linux-gnu/clippy, rust/x86_64-unknown-linux-gnu/test]
  ci.yml:lint:
    - rust/all/fmt
"""


def _git(repo: Path, *args: str) -> str:
    proc = run_captured(["git", "-C", str(repo), *args])
    if not proc.ok:
        raise subprocess.CalledProcessError(proc.returncode, ["git", *args], proc.stdout, proc.stderr)
    return proc.stdout.strip()


class MiniYamlTest(unittest.TestCase):
    def test_subset_parses(self) -> None:
        doc = parse(DEFINITION)
        assert isinstance(doc, dict)
        gates = doc["gates"]
        assert isinstance(gates, dict)
        self.assertEqual(gates["rust/x86_64-unknown-linux-gnu/test"], {"lane": "tests"})
        jobs = doc["jobs"]
        assert isinstance(jobs, dict)
        self.assertEqual(jobs["ci.yml:lint"], ["rust/all/fmt"])

    def test_quoted_hash_is_not_a_comment(self) -> None:
        self.assertEqual(parse("a: 'x # y'  # real comment\n"), {"a": "x # y"})

    def test_ambiguous_constructs_are_rejected(self) -> None:
        for bad in ("a: &x 1", "a: *x", "a: !!str 1", "<<: {a: 1}", "a: |\n  x", "a: 1\n---\nb: 2",
                    "a: 1\na: 2", "a: {b: {c: 1}}", "- {a: 1}\n- b: 2"):
            with self.subTest(bad=bad), self.assertRaises(MiniYamlError):
                parse(bad)


class DefinitionTest(unittest.TestCase):
    def test_gate_paths_follow_the_platform_schema(self) -> None:
        self.assertIsNone(validate_gate_path("rust/all/clippy"))
        self.assertIsNone(validate_gate_path("rust/aarch64-apple-darwin/dylint"))
        self.assertIsNone(validate_gate_path("python/3.13/tests"))
        self.assertIsNotNone(validate_gate_path("rust/linux/clippy"))  # not a triple
        self.assertIsNotNone(validate_gate_path("go/all/vet"))
        self.assertIsNotNone(validate_gate_path("rust/all"))

    def test_definition_and_lane_cross_check(self) -> None:
        loaded = parse_definition(DEFINITION, lanes=("rust", "tests"))
        assert loaded.definition is not None
        self.assertEqual([g.path for g in loaded.definition.gates_of_lane("rust")],
                         ["rust/all/fmt", "rust/x86_64-unknown-linux-gnu/clippy"])
        self.assertEqual([f.message for f in loaded.findings],
                         ["gate 'rust/x86_64-pc-windows-msvc/clippy' names lane 'cross', which the local gate does not declare"])

    def test_job_listing_an_undeclared_gate(self) -> None:
        loaded = parse_definition("version: 1\ngates:\n  rust/all/fmt: {lane: rust}\njobs:\n  ci.yml:lint: [rust/all/nope]\n")
        self.assertIn("undeclared gate(s) rust/all/nope", loaded.findings[0].message)


class GitCase(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not on PATH")
        self.repo = Path(tempfile.mkdtemp())
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@example.com")
        _git(self.repo, "config", "user.name", "t")
        loaded = parse_definition(DEFINITION)
        assert loaded.definition is not None
        self.definition = loaded.definition

    def tearDown(self) -> None:
        shutil.rmtree(self.repo, ignore_errors=True)

    def commit(self, name: str, *, trailers: tuple[str, ...] = ()) -> str:
        (self.repo / f"{name}.txt").write_text(name, encoding="utf-8")
        _git(self.repo, "add", "-A")
        message = name + ("\n\n" + "\n".join(trailers) if trailers else "")
        _git(self.repo, "commit", "-q", "-m", message)
        return _git(self.repo, "rev-parse", "HEAD")

    def attest(self, gate: str, lane: str, *, sha: str = "HEAD") -> str:
        tree = _git(self.repo, "rev-parse", f"{sha}^{{tree}}")
        parents = tuple(_git(self.repo, "log", "-1", "--format=%P", sha).split())
        return make(gate, tree=tree, parents=parents, lane=lane, key="k" * 64, via="run", secs=3).trailer()

    def amend_with(self, *trailers: str) -> str:
        message = _git(self.repo, "log", "-1", "--format=%B")
        _git(self.repo, "commit", "-q", "--amend", "-m", message + "\n\n" + "\n".join(trailers))
        return _git(self.repo, "rev-parse", "HEAD")


class VerifyTest(GitCase):
    def test_valid_missing_and_job_decisions(self) -> None:
        self.commit("base")
        self.commit("feature")
        head = self.amend_with(self.attest("rust/all/fmt", "rust"),
                               self.attest("rust/x86_64-unknown-linux-gnu/clippy", "rust"))
        result = verify_commit(self.repo, head, self.definition)
        self.assertEqual(result.state("rust/all/fmt"), VALID)
        self.assertEqual(result.state("rust/x86_64-unknown-linux-gnu/test"), MISSING)  # omitted = not run
        jobs = decide_jobs(self.definition, ("ci.yml:build-linux-x64", "ci.yml:lint"), head_trusted=True, commit=result)
        self.assertEqual([(j.job, j.skip) for j in jobs], [("ci.yml:build-linux-x64", False), ("ci.yml:lint", True)])
        self.assertIn("rust/x86_64-unknown-linux-gnu/test=missing", jobs[0].reason)
        untrusted = decide_jobs(self.definition, ("ci.yml:lint",), head_trusted=False, commit=result)
        self.assertFalse(untrusted[0].skip)

    def test_tampering_and_wrong_content_are_caught(self) -> None:
        self.commit("base")
        self.commit("feature")
        good = self.attest("rust/all/fmt", "rust")
        tampered = good.replace('"secs":3', '"secs":4')
        head = self.amend_with(
            tampered,
            self.attest("rust/x86_64-unknown-linux-gnu/clippy", "tests"),  # wrong lane
            self.attest("rust/all/undeclared", "rust"),
        )
        states = {s.gate: s.state for s in verify_commit(self.repo, head, self.definition).statuses}
        self.assertEqual(states["rust/all/fmt"], "bad-stamp")
        self.assertEqual(states["rust/x86_64-unknown-linux-gnu/clippy"], "lane-mismatch")
        self.assertEqual(states["rust/all/undeclared"], "undeclared")

    def test_attestation_does_not_survive_a_content_change_or_rebase(self) -> None:
        base = self.commit("base")
        self.commit("feature", trailers=())
        trailer = self.attest("rust/all/fmt", "rust")
        # Same message, different content: tree differs.
        (self.repo / "feature.txt").write_text("changed", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "--amend", "-m", "feature\n\n" + trailer)
        self.assertEqual(verify_commit(self.repo, "HEAD", self.definition).state("rust/all/fmt"), "wrong-tree")
        # Same tree, different parent (a rebase): parents differ.
        _git(self.repo, "checkout", "-q", "-b", "other", base)
        self.commit("other")
        _git(self.repo, "cherry-pick", "main")
        self.assertIn(verify_commit(self.repo, "HEAD", self.definition).state("rust/all/fmt"),
                      ("wrong-parents", "wrong-tree"))

    def test_compact_trailer_round_trips(self) -> None:
        self.commit("base")
        line = self.attest("rust/all/fmt", "rust")
        self.assertNotIn(" ", line.split(": ", 1)[1])  # compact JSON
        parsed = parse_trailers("x\n\n" + line + "\nCi-Attestation: {not json}\n")
        assert parsed[0].attestation is not None
        self.assertEqual(parsed[0].attestation.compute_stamp(), parsed[0].attestation.stamp)
        self.assertIsNotNone(parsed[1].error)
        changed = replace(parsed[0].attestation, secs=99)
        self.assertNotEqual(changed.compute_stamp(), changed.stamp)


class KeysPriorityTest(GitCase):
    def test_job_mapped_gates_are_published_first_and_overflow_is_reported(self) -> None:
        import contextlib
        import io
        import os

        from ci_lint.attest_cli import register

        self.commit("base")
        self.commit("feature")
        gates = [("rust/all/fmt", "rust"), ("rust/x86_64-unknown-linux-gnu/clippy", "rust"),
                 ("rust/x86_64-unknown-linux-gnu/test", "tests"), ("rust/x86_64-pc-windows-msvc/clippy", "cross")]
        definition = DEFINITION.replace("{lane: cross}", "{lane: cross}")
        (self.repo / "ci-attestations.yml").write_text(definition, encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "def")
        self.amend_with(*(self.attest(g, lane) for g, lane in gates))
        import argparse
        parser = argparse.ArgumentParser()
        register(parser.add_subparsers(dest="cmd"))
        out_dir = self.repo / "out"
        args = parser.parse_args(["attest", "keys", "--repo", str(self.repo), "--main-ref", "main",
                                  "--out-dir", str(out_dir), "--slots", "2"])
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(args.func(args), 0)
        printed = out.getvalue()
        # Both slots go to gates mapped to a job (fmt, linux clippy/test), never
        # to the unmapped windows clippy gate.
        self.assertNotIn("x86_64-pc-windows-msvc", printed)
        self.assertIn("not published", err.getvalue())
        self.assertIn("rust/x86_64-pc-windows-msvc/clippy", err.getvalue())
        self.assertFalse(os.environ.get("GITHUB_ACTIONS") == "true" and "::warning" not in printed)


class LineageTest(GitCase):
    def test_labels_parse_and_order_without_git(self) -> None:
        main = Lineage(main_ordinal=2189, sha="6f6b886c32" + "0" * 30)
        pr = Lineage(main_ordinal=2189, sha="18204251ce" + "0" * 30, pr=3532, ordinal=1)
        self.assertEqual(main.label(), "m2189-6f6b886c32")
        self.assertEqual(pr.label(), "m2189-c1-18204251ce-pr-3532")
        self.assertEqual(pr.stem(), "m2189-c1-18204251ce")
        entry = parse_key("att1-rust.x86_64-unknown-linux-gnu.test-m2189-c1-18204251ce-pr-3532")
        assert entry is not None
        self.assertEqual((entry.family, entry.lineage.pr, entry.lineage.ordinal),
                         ("att1-rust.x86_64-unknown-linux-gnu.test", 3532, 1))
        self.assertTrue(main.may_precede(pr))
        self.assertFalse(Lineage(main_ordinal=2191, sha="a" * 40).may_precede(pr))  # after the PR's base
        self.assertFalse(Lineage(main_ordinal=2189, sha="b" * 40, pr=3532, ordinal=2).may_precede(pr))
        self.assertIsNone(parse_key("zccache-unit-v1-x86_64-unknown-linux-gnu-1234-36954283026"))
        self.assertIsNone(parse_key("fam-m5-c2-aaaaaaaaaa"))  # a PR ordinal without its tag
        main_entry = parse_key("x86_64-unknown-linux-gnu-m12-bbbbbbbbbb")
        assert main_entry is not None
        self.assertEqual((main_entry.family, main_entry.lineage.main_ordinal), ("x86_64-unknown-linux-gnu", 12))

    def test_resolve_prefers_the_nearest_verified_ancestor(self) -> None:
        m1 = self.commit("m1")
        m2 = self.commit("m2")
        _git(self.repo, "checkout", "-q", "-b", "pr")
        c1 = self.commit("c1")
        _git(self.repo, "checkout", "-q", "main")
        m3 = self.commit("m3")  # after the PR's base: not an ancestor
        _git(self.repo, "checkout", "-q", "pr")
        c2 = self.commit("c2")
        head = lineage_of(self.repo, c2, main_ref="main", pr=7)
        self.assertEqual(head.label(), f"m2-c2-{c2[:10]}-pr-7")
        fam = "cache-v1"
        keys = [f"{fam}-m1-{m1[:10]}", f"{fam}-m2-{m2[:10]}", f"{fam}-m3-{m3[:10]}",
                f"{fam}-m2-c1-{c1[:10]}-pr-7", f"{fam}-m2-c1-{'f' * 10}-pr-7", f"{fam}-m2-c1-{c1[:10]}-pr-8",
                "other-m2-" + m2[:10]]
        self.assertEqual(resolve(self.repo, head, keys, family=fam).key, f"{fam}-m2-c1-{c1[:10]}-pr-7")
        only_main = resolve(self.repo, head, keys, family=fam, required_shas=frozenset({m1[:10], m2[:10], m3[:10]}))
        self.assertEqual(only_main.key, f"{fam}-m2-{m2[:10]}")
        self.assertIsNone(resolve(self.repo, head, [f"{fam}-m3-{m3[:10]}"], family=fam).key)


class LocalGateEmitsTest(GitCase):
    def test_run_stamps_one_trailer_per_gate_of_each_passed_lane(self) -> None:
        py = sys.executable
        (self.repo / "gate.py").write_text("import sys\n", encoding="utf-8")
        lanes = "".join(f'[gate.lanes.{lane}]\nrun = ["{py}", "gate.py", "--lane", "{lane}"]\ntools = ["git"]\n\n'
                        for lane in ("rust", "tests"))
        (self.repo / "local-gate.toml").write_text(f'[gate]\nrun = ["{py}", "gate.py"]\n\n{lanes}', encoding="utf-8")
        (self.repo / "ci-attestations.yml").write_text(DEFINITION.replace("{lane: cross}", "{lane: rust}"),
                                                       encoding="utf-8")
        self.commit("init")
        loaded = load_gate_config(self.repo)
        assert loaded.config is not None
        outcome = run_gate(self.repo, loaded.config, use_cache=False)
        self.assertEqual(outcome.exit_code, 0, outcome.message)
        result = verify_commit(self.repo, "HEAD", parse_definition(DEFINITION.replace("{lane: cross}", "{lane: rust}")).definition)  # type: ignore[arg-type]
        self.assertEqual(sorted(s.state for s in result.statuses), [VALID] * 4)
        # Re-stamping replaces, never duplicates, the trailers.
        run_gate(self.repo, loaded.config, use_cache=False, force=True)
        message = _git(self.repo, "log", "-1", "--format=%B")
        self.assertEqual(message.count("Ci-Attestation:"), 4)
        self.assertEqual(message.count("Local-Gate:"), 1)


if __name__ == "__main__":
    unittest.main()
