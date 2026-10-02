"""GATE-007 -- ci_lint/lane_cache.py and the laned `local-gate run` (zackees/ci.yml#177)."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ci_lint.finding import Status
from ci_lint.lane_cache import (
    LaneConfig,
    ToolVersion,
    ToolVersions,
    audit_trace,
    cache_dir,
    check_lanes_static,
    lane_key,
    lookup,
    record,
    run_audit,
    tree_entries,
)
from ci_lint.local_gate import (
    Attestation,
    check_commit,
    load_gate_config,
    parse_attestation,
    run_gate,
)
from ci_lint.proc import run_captured

TREE = "a" * 40


def _git(repo: Path, *args: str) -> str:
    proc = run_captured(["git", "-C", str(repo), *args])
    if not proc.ok:
        raise subprocess.CalledProcessError(proc.returncode, ["git", *args], proc.stdout, proc.stderr)
    return proc.stdout.strip()


GATE_PY = """\
import pathlib, sys
lane = sys.argv[sys.argv.index("--lane") + 1]
log = pathlib.Path(sys.argv[sys.argv.index("--log") + 1])
log.write_text(log.read_text() + lane + "\\n" if log.exists() else lane + "\\n")
import time
if pathlib.Path("sleep").exists():
    time.sleep(float(pathlib.Path("sleep").read_text()))
if pathlib.Path("fail-" + lane).exists():
    raise SystemExit(4)
if pathlib.Path("na-" + lane).exists():
    raise SystemExit(75)
"""


class LanedRepo(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not on PATH")
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.log = self.tmp / "lanes.log"
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@example.com")
        _git(self.repo, "config", "user.name", "t")
        py = sys.executable
        (self.repo / "local-gate.toml").write_text(
            f'[gate]\nrun = ["{py}", "gate.py"]\n\n'
            f'[gate.lanes.lint]\nrun = ["{py}", "gate.py", "--lane", "lint", "--log", "{self.log}"]\n'
            'exclude = ["docs/**"]\ntools = ["git"]\n\n'
            f'[gate.lanes.tests]\nrun = ["{py}", "gate.py", "--lane", "tests", "--log", "{self.log}"]\n'
            'exclude = ["docs/**", "**/*.py"]\nkeep = ["src/**"]\ntools = ["git"]\n',
            encoding="utf-8",
        )
        self.write("gate.py", GATE_PY)
        self.write("src/lib.rs", "fn a() {}\n")
        self.write("src/helper.py", "X = 1\n")
        self.write("tools/guard.py", "Y = 1\n")
        self.write("docs/readme.md", "hi\n")
        self.write("Cargo.lock", "# lock\n")
        self.commit("init")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel: str, text: str) -> None:
        (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / rel).write_text(text, encoding="utf-8")

    def commit(self, msg: str) -> None:
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", msg)

    def config(self):  # type: ignore[no-untyped-def]
        loaded = load_gate_config(self.repo)
        config, findings = loaded.config, loaded.findings
        self.assertEqual(findings, [])
        assert config is not None
        return config

    def runs(self) -> list[str]:
        return self.log.read_text().split() if self.log.exists() else []

    def gate(self, **kw: bool) -> str:
        self.log.unlink(missing_ok=True)
        outcome = run_gate(self.repo, self.config(), **kw)
        self.assertEqual(outcome.exit_code, 0, outcome.message)
        att = parse_attestation(_git(self.repo, "log", "-1", "--format=%B"))
        assert att is not None and att.lanes is not None
        return att.lanes


class OptionalLaneTest(LanedRepo):
    """A lane that cannot run on every host (zackees/ci.yml#202: a local VM)."""

    def _make_tests_optional(self) -> None:
        text = (self.repo / "local-gate.toml").read_text(encoding="utf-8")
        (self.repo / "local-gate.toml").write_text(
            text.replace('keep = ["src/**"]\ntools = ["git"]\n', 'keep = ["src/**"]\ntools = ["git"]\noptional = true\n'),
            encoding="utf-8")

    def test_optional_lane_not_applicable_passes_unattested_and_uncached(self) -> None:
        self._make_tests_optional()
        self.write("na-tests", "")
        self.commit("tests n/a here")
        self.assertEqual(self.gate(), "lint:run,tests:n/a")
        # Never cached: once the host can run it, it runs.
        _git(self.repo, "rm", "-q", "na-tests")
        self.commit("host gained the VM")
        self.assertEqual(self.gate(), "lint:run,tests:run")
        self.assertIn("tests", self.runs())

    def test_exit_75_from_a_required_lane_is_a_failure(self) -> None:
        self.write("na-tests", "")
        self.commit("tests n/a but required")
        self.log.unlink(missing_ok=True)
        self.assertEqual(run_gate(self.repo, self.config()).exit_code, 75)


class LaneRunTest(LanedRepo):
    def test_first_run_runs_every_lane_and_records_provenance(self) -> None:
        self.assertEqual(self.gate(), "lint:run,tests:run")
        self.assertEqual(self.runs(), ["lint", "tests"])
        self.assertEqual(check_commit(self.repo, "HEAD").state, "attested")

    def test_docs_only_change_reuses_every_lane(self) -> None:
        self.gate()
        self.write("docs/readme.md", "changed\n")
        self.commit("docs")
        lanes = self.gate()
        self.assertEqual(self.runs(), [])
        self.assertRegex(lanes, r"^lint:reused@[0-9a-f]{12},tests:reused@[0-9a-f]{12}$")

    def test_python_guard_change_reruns_lint_only(self) -> None:
        self.gate()
        self.write("tools/guard.py", "Y = 2\n")
        self.commit("guard")
        self.assertRegex(self.gate(), r"^lint:run,tests:reused@")
        self.assertEqual(self.runs(), ["lint"])

    def test_keep_overrides_exclude(self) -> None:
        self.gate()
        self.write("src/helper.py", "X = 2\n")  # *.py is excluded from tests, src/** is kept
        self.commit("helper")
        self.assertEqual(self.gate(), "lint:run,tests:run")

    def test_mandatory_input_cannot_be_excluded(self) -> None:
        self.gate()
        self.write("Cargo.lock", "# lock 2\n")
        self.commit("lock")
        self.assertEqual(self.gate(), "lint:run,tests:run")

    def test_a_failed_lane_is_never_cached(self) -> None:
        self.write("fail-tests", "")
        self.commit("make tests fail")
        self.log.unlink(missing_ok=True)
        outcome = run_gate(self.repo, self.config())
        self.assertEqual(outcome.exit_code, 4)
        self.assertEqual(self.runs(), ["lint", "tests"])
        self.assertEqual(check_commit(self.repo, "HEAD").state, "missing")
        _git(self.repo, "rm", "-q", "fail-tests")
        self.commit("fix")
        self.assertRegex(self.gate(), r"^lint:run,tests:run$")  # fail-tests was a lint input too

    def test_no_cache_and_revert_to_a_passed_state(self) -> None:
        self.gate()
        first = _git(self.repo, "rev-parse", "HEAD^{tree}")
        self.write("src/lib.rs", "fn b() {}\n")
        self.commit("edit")
        self.gate()
        self.write("src/lib.rs", "fn a() {}\n")  # back to the first tree's content
        self.commit("revert")
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD^{tree}"), first)
        self.assertRegex(self.gate(force=True), r"^lint:reused@.*,tests:reused@")
        self.assertEqual(self.gate(force=True, use_cache=False), "lint:run,tests:run")

    def test_cache_lives_in_the_shared_git_dir(self) -> None:
        self.gate()
        self.assertTrue(str(cache_dir(self.repo)).endswith(".git/ci-lint/lane-cache"))
        self.assertEqual(len(list((cache_dir(self.repo) / "lint").glob("*.json"))), 1)


class ParallelTest(LanedRepo):
    """Light lanes run alongside the heavy chain (#177, soldr follow-up)."""

    def _three_lanes(self, *, light: bool) -> None:
        py = sys.executable
        weight = 'weight = "light"\n' if light else ""
        lanes = "".join(
            f'[gate.lanes.{lane}]\nrun = ["{py}", "gate.py", "--lane", "{lane}", "--log", "{self.log}"]\n'
            f'tools = ["git"]\n{weight if lane != "heavy" else ""}\n'
            for lane in ("a", "b", "heavy")
        )
        (self.repo / "local-gate.toml").write_text(f'[gate]\nrun = ["{py}", "gate.py"]\n\n{lanes}', encoding="utf-8")
        self.write("sleep", "1.5")
        self.commit("three lanes")

    def test_light_lanes_overlap_the_heavy_chain(self) -> None:
        self._three_lanes(light=True)
        start = time.monotonic()
        self.assertEqual(self.gate(), "a:run,b:run,heavy:run")  # provenance keeps declared order
        self.assertLess(time.monotonic() - start, 3.5)  # sequential would be >= 4.5 s

    def test_heavy_lanes_stay_sequential(self) -> None:
        self._three_lanes(light=False)
        start = time.monotonic()
        self.gate()
        self.assertGreaterEqual(time.monotonic() - start, 4.4)

    def test_passing_lanes_are_cached_even_when_another_lane_fails(self) -> None:
        self._three_lanes(light=True)
        self.write("fail-b", "")
        self.write("sleep", "0")
        self.commit("b fails")
        self.log.unlink(missing_ok=True)
        self.assertEqual(run_gate(self.repo, self.config()).exit_code, 4)
        # Same tree, no cache bypass: a and heavy were recorded, so only b reruns.
        self.log.unlink(missing_ok=True)
        run_gate(self.repo, self.config())
        self.assertEqual(self.runs(), ["b"])

    def test_bad_weight(self) -> None:
        text = (self.repo / "local-gate.toml").read_text(encoding="utf-8").replace(
            'tools = ["git"]\n\n[gate.lanes.tests]', 'tools = ["git"]\nweight = "medium"\n\n[gate.lanes.tests]'
        )
        (self.repo / "local-gate.toml").write_text(text, encoding="utf-8")
        self.assertEqual([f.rule for f in load_gate_config(self.repo).findings], ["GATE-007"])


class KeyTest(LanedRepo):
    def test_key_depends_on_tool_version_env_and_argv(self) -> None:
        cfg = self.config()
        lane = cfg.lanes[0]
        entries = tree_entries(self.repo, "HEAD")
        base = dict(gate_run=cfg.run, gate_source=cfg.source)
        v1 = ToolVersions([ToolVersion("git", "git 1")])
        v2 = ToolVersions([ToolVersion("git", "git 2")])
        k = lane_key(entries, lane, versions=v1, **base).key
        self.assertNotEqual(k, lane_key(entries, lane, versions=v2, **base).key)
        with_env = LaneConfig(lane.id, lane.run, lane.exclude, lane.keep, lane.tools, ("FOO",))
        self.assertNotEqual(
            lane_key(entries, with_env, versions=v1, environ={"FOO": "1"}, **base).key,
            lane_key(entries, with_env, versions=v1, environ={"FOO": "2"}, **base).key,
        )
        moved = LaneConfig(lane.id, (*lane.run, "--extra"), lane.exclude, lane.keep, lane.tools)
        self.assertNotEqual(k, lane_key(entries, moved, versions=v1, **base).key)

    def test_pins_follow_the_lane_tools(self) -> None:
        # Design revision 3 (#177): a Python-only lane is not invalidated by
        # Cargo.lock; a Rust lane and an unrecognized-tool lane are.
        cfg = self.config()
        base = dict(gate_run=cfg.run, gate_source=cfg.source, versions=ToolVersions([ToolVersion("uv", "1"),
                    ToolVersion("soldr", "1"), ToolVersion("git", "1")]))
        before = tree_entries(self.repo, "HEAD")
        self.write("Cargo.lock", "# lock 2\n")
        self.commit("lock")
        after = tree_entries(self.repo, "HEAD")
        def lane(tools: tuple[str, ...]) -> LaneConfig:
            return LaneConfig("x", cfg.run, exclude=("Cargo.lock",), tools=tools)
        py = lane(("uv",))
        self.assertEqual(lane_key(before, py, **base).key, lane_key(after, py, **base).key)
        for tools in (("soldr",), ("git",)):
            rs = lane(tools)
            self.assertNotEqual(lane_key(before, rs, **base).key, lane_key(after, rs, **base).key)

    def test_expired_entry_is_ignored(self) -> None:
        lane = LaneConfig("lint", ("x",), max_age_hours=1.0)
        record(self.repo, lane, "k" * 64, secs=3, head="h", tree="t")
        self.assertIsNotNone(lookup(self.repo, lane, "k" * 64))
        self.assertIsNone(lookup(self.repo, lane, "k" * 64, now=time.time() + 2 * 3600))

    def test_trailer_round_trip(self) -> None:
        att = Attestation(tree=TREE, secs=5, lanes="lint:run,tests:reused@abcdef012345")
        self.assertEqual(parse_attestation(f"s\n\n{att.trailer()}\n"), att)
        legacy = parse_attestation(f"s\n\nLocal-Gate: v1 tree={TREE} secs=5\n")
        assert legacy is not None and legacy.lanes is None


class AuditTest(LanedRepo):
    def test_trace_reads_of_excluded_files_are_reported(self) -> None:
        cfg = self.config()
        lane = cfg.lanes[1]  # tests: excludes docs/** and **/*.py, keeps src/**
        root = str(self.repo.resolve())
        trace = "\n".join(
            [
                f'123 openat(AT_FDCWD, "{root}/docs/readme.md", O_RDONLY) = 3',
                f'123 openat(AT_FDCWD, "{root}/src/helper.py", O_RDONLY) = 3',
                f'123 openat(AT_FDCWD, "{root}/tools/guard.py", O_RDONLY) = -1 ENOENT (No such file)',
                f'123 openat(AT_FDCWD, "{root}/Cargo.lock", O_RDONLY) = 3',
                f'123 openat(AT_FDCWD, "{root}/gate.py", O_RDONLY) = 3',
            ]
        )
        audit = audit_trace(self.repo, lane, cfg.run, cfg.source, trace)
        self.assertEqual(audit.excluded_reads, ("docs/readme.md",))
        self.assertEqual(audit.opened, 4)

    @unittest.skipUnless(shutil.which("strace"), "strace not on PATH")
    def test_live_audit_of_a_clean_lane(self) -> None:
        cfg = self.config()
        audit = run_audit(self.repo, cfg.lanes[0], cfg.run, cfg.source)
        assert audit is not None
        self.assertEqual(audit.excluded_reads, ())
        self.assertGreater(audit.opened, 0)


class StaticTest(LanedRepo):
    def test_green(self) -> None:
        cfg = self.config()
        self.assertEqual(check_lanes_static(cfg.lanes, cfg.run, cfg.source, self.repo), [])

    def test_exclusions_that_drop_mandatory_inputs(self) -> None:
        cfg = self.config()
        bad = (LaneConfig("x", (*cfg.run, "--lane", "x"), exclude=("**/*.lock", "gate.py"), tools=("git",)),)
        findings = check_lanes_static(bad, cfg.run, cfg.source, self.repo)
        # Only the exclusion that names a mandatory input is reported; the broad
        # `**/*.lock` is harmless because mandatory inputs are re-included.
        self.assertEqual([f.rule for f in findings], ["GATE-007"])
        self.assertIn("gate.py", findings[0].message)
        self.assertNotIn("Cargo.lock", findings[0].message)

    def test_lane_must_slice_the_gate_command_and_declare_tools(self) -> None:
        cfg = self.config()
        lanes = (LaneConfig("x", ("bash", "other.sh")),)
        statuses = sorted(f.status.value for f in check_lanes_static(lanes, cfg.run, cfg.source, self.repo))
        self.assertEqual(statuses, [Status.NEEDS_REVIEW.value, Status.VIOLATION.value])

    def test_unknown_lane_key_is_ct_001(self) -> None:
        text = (self.repo / "local-gate.toml").read_text(encoding="utf-8") + "bogus = 1\n"
        (self.repo / "local-gate.toml").write_text(text, encoding="utf-8")
        findings = load_gate_config(self.repo).findings
        self.assertEqual([f.rule for f in findings], ["CT-001"])


if __name__ == "__main__":
    unittest.main()
