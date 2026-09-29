"""RUST-017: `--no-capture` / a single test thread serializes a CI test run.

zackees/ci.yml#79 (evidence: zackees/running-process#1231). `cargo nextest
run --no-capture` implies `--test-threads=1`: a repo that adds it "so
println!s show up" silently turns a parallel suite serial. running-process's
2197 per-test durations summed to 318.5 s against a 318.6 s wall; dropping
the flag took nextest 318.6 s -> 181.8 s. nextest's own
`failure-output = "immediate-final"` already prints a failing or timed-out
test's output, so the flag buys nothing CI needs.

Static signals (run: lines, one level into a referenced ci/*.py script):

- violation: a `cargo nextest run` / `cargo test` invocation carrying
  `--no-capture`/`--nocapture` (before or after `--`), or `--test-threads 1`.
- needs_review: a referenced ci/*.py script that builds a test command and
  holds a `--no-capture`/`--nocapture` string it appends dynamically (the
  running-process shape: an env-var opt-in) -- ci-lint cannot see whether
  the opt-in is on in CI.

A same-line `# ci-lint: allow RUST-017 <reason>` (e.g. a Windows-only
serialization with a documented cause) excuses that line.
"""

from __future__ import annotations

from pathlib import Path

from ci_lint.finding import Finding, Status
from ci_lint.rules.test_invocations import (
    allowed,
    find_test_invocation,
    iter_test_invocations,
    referenced_scripts,
    script_calls,
    script_string_constants,
    source_line,
)

RULE = "RUST-017"
NOCAPTURE_FLAGS: frozenset[str] = frozenset({"--no-capture", "--nocapture"})

_FIX = (
    "drop the flag from CI; configure nextest's profile instead ('[profile.ci] failure-output = "
    '"immediate-final"\' in .config/nextest.toml, optionally success-output) so failing and '
    "timed-out tests still print their output while the suite stays parallel. Keep --no-capture "
    "as a local debugging opt-in only"
)


def _serializing_flag(args: tuple[str, ...]) -> str | None:
    for i, arg in enumerate(args):
        if arg in NOCAPTURE_FLAGS:
            return arg
        if arg == "--test-threads=1":
            return arg
        if arg == "--test-threads" and i + 1 < len(args) and args[i + 1] == "1":
            return "--test-threads 1"
    return None


def check_rust_017(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for inv in iter_test_invocations(repo_root):
        flag = _serializing_flag(inv.args)
        if flag is None or allowed(inv.site.raw, RULE):
            continue
        what = "cargo nextest run" if inv.kind == "nextest" else "cargo test"
        findings.append(
            Finding(
                rule=RULE,
                path=inv.site.path,
                line=inv.site.line,
                message=f"{inv.site.loc}: '{what}' passes {flag}, which forces the test run serial "
                "(one test at a time on a multi-core runner)",
                fix=_FIX,
            )
        )

    flagged_paths = {f.path for f in findings}
    for rel in referenced_scripts(repo_root):
        script = repo_root / rel
        builds_tests = any(find_test_invocation(c.tokens) is not None for c in script_calls(script))
        constants = script_string_constants(script)
        if not builds_tests:
            builds_tests = any(v in ("nextest", "test") for v, _ in constants) and any(
                v == "cargo" or v.endswith("/cargo") for v, _ in constants
            )
        if not builds_tests:
            continue
        if rel in flagged_paths:
            continue  # the literal argv is already a violation above
        for value, lineno in constants:
            if value not in NOCAPTURE_FLAGS:
                continue
            if allowed(source_line(script, lineno), RULE):
                continue
            findings.append(
                Finding(
                    rule=RULE,
                    path=rel,
                    line=lineno,
                    status=Status.NEEDS_REVIEW,
                    message=f"line {lineno}: this CI script can append '{value}' to its test command; "
                    "cannot statically confirm the opt-in is off in CI",
                    fix="make sure no workflow turns this opt-in on (it serializes the suite); if it is "
                    "a local-debugging switch, say so with a same-line '# ci-lint: allow RUST-017 "
                    "<reason>' comment. " + _FIX,
                )
            )
    return findings
