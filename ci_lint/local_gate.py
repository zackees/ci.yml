"""Local gate first (GATE-001..005, zackees/ci.yml#166, #168).

A pull request should pass remote CI on its first push. The cheapest way to
get there is to make the remote quick gate a *subset* of one local command,
and to refuse remote runner time for a head commit that never passed that
command. This module is the whole mechanism, in four pieces:

- **Declaration.** `[local.gate]` in `ci.toml`, or `[gate]` in a repo-root
  `local-gate.toml` for a repository that has not adopted `ci.toml` (the
  same table either way, parsed by `parse_gate_table`). `run` is the gate's
  argv; `mirrors` names the remote jobs whose `run:` steps must invoke that
  same argv and nothing else; `verify` names the PR-entry job that checks
  the attestation before any other job starts.
- **Attestation.** `ci-lint local-gate run` requires a clean tracked tree,
  runs the gate, refuses if the gate rewrote a tracked file, and on success
  amends HEAD's message with a `Local-Gate: v1 tree=<tree sha> secs=<n>`
  trailer. The trailer is bound to the commit's *tree*, so any amend or
  rebase that changes content invalidates it, while amending only the
  message (which this command itself does) keeps it valid.
- **Enforcement.** `ci-lint local-gate verify` (GATE-003) runs first in the
  PR-entry workflow and fails in seconds when the PR head is not attested
  for its exact tree; every other job `needs:` it (GATE-002), so an
  unattested push costs one small job, not a fan-out. `ci-lint local-gate
  check-push` is the matching git pre-push hook (`local-gate hook install`),
  which stops the push before it reaches GitHub at all.
- **Measurement.** `ci-lint local-gate first-pass` (GATE-004, live, GET-only)
  reports the share of merged PRs whose first pushed head went green on its
  first attempt with no further pushes -- the number this whole mechanism
  exists to move.

The trailer is an honesty contract, not a cryptographic proof: anyone can
type it. What keeps it honest is that the mirrored remote jobs still run
the identical command (GATE-001), so a forged or stale attestation turns
into a visible "attested but red remotely" run in `first-pass`, never into
a silent pass.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ci_lint.finding import Finding, Status
from ci_lint.gate_isolation import IsolationConfig, check_isolation, parse_isolation
from ci_lint.lane_cache import (
    LaneConfig,
    ToolVersions,
    check_lanes_static,
    lane_key,
    lookup,
    parse_lanes,
    record,
    tree_entries,
)
from ci_lint.toml_cursor import Cursor, TomlValue
from ci_lint.yaml_io import YamlValue

GATE_FILE = "local-gate.toml"
TRAILER_KEY = "Local-Gate"
TRAILER_VERSION = "v1"
MODES: tuple[str, ...] = ("enforce", "shadow")
# Bot authors cannot run a local gate; their PRs get the full remote run.
DEFAULT_EXEMPT_AUTHORS: tuple[str, ...] = ("dependabot[bot]", "renovate[bot]", "github-actions[bot]")
# `uses:` actions that only prepare a mirrored job's environment. Any other
# `uses:` step in a mirrored job could run a remote-only check, so it must
# be named in `setup-steps` or it is reported (GATE-001).
SETUP_ACTIONS: frozenset[str] = frozenset(
    {
        "actions/checkout",
        "actions/setup-python",
        "actions/setup-node",
        "actions/setup-go",
        "actions/cache",
        "actions/cache/restore",
        "astral-sh/setup-uv",
        "dtolnay/rust-toolchain",
        "zackees/setup-soldr",
    }
)
VERIFY_TOKEN = "local-gate verify"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_TRAILER_RE = re.compile(rf"^{TRAILER_KEY}:\s*(?P<body>.+?)\s*$", re.MULTILINE)
_SHELL_BOILERPLATE = re.compile(r"^(set\s+-[a-zA-Z]+(\s+\S+)*|shopt\s.*|cd\s+\S+)$")


@dataclass(frozen=True)
class JobRef:
    """`<workflow file>:<job id>`, e.g. `ci.yml:lint`."""

    workflow: str
    job: str

    @staticmethod
    def parse(text: str) -> JobRef | None:
        workflow, sep, job = text.partition(":")
        if not sep or not workflow or not job or "/" in workflow:
            return None
        return JobRef(workflow=workflow, job=job)

    def __str__(self) -> str:
        return f"{self.workflow}:{self.job}"


@dataclass(frozen=True)
class GateConfig:
    run: tuple[str, ...]
    mirrors: tuple[JobRef, ...]
    setup_steps: frozenset[str]
    verify: JobRef | None
    verify_exempt: frozenset[str]
    exempt_authors: frozenset[str]
    mode: str
    source: str
    # GATE-005 (zackees/ci.yml#168): how a self-hosted tool's suite runs isolated.
    isolation: IsolationConfig | None = None
    # GATE-007 (zackees/ci.yml#177): the gate split into cacheable lanes, in
    # declared order. Empty: `run` executes as one opaque command.
    lanes: tuple[LaneConfig, ...] = ()

    @property
    def command(self) -> str:
        return shlex.join(self.run)


def parse_gate_table(raw: dict[str, TomlValue], *, path: str, source: str, findings: list[Finding]) -> GateConfig | None:
    """Strictly parse one gate table. Unknown keys are CT-001 and type
    errors CT-002 (the shared `Cursor` contract); semantic errors are
    GATE-001, since an unusable declaration means remote ⊆ local is
    unproven."""

    sub = Cursor(raw, path, findings, source)
    run = sub.list_str("run")
    mirrors_raw = sub.list_str("mirrors", required=False)
    setup_steps = sub.list_str("setup-steps", required=False)
    verify_raw = sub.str_("verify", required=False)
    verify_exempt = sub.list_str("verify-exempt", required=False)
    exempt_authors = sub.list_str("exempt-authors", required=False, default=DEFAULT_EXEMPT_AUTHORS)
    mode = sub.str_("mode", required=False, default="enforce") or "enforce"
    isolation_raw = sub.table_("isolation", required=False)
    lanes_raw = sub.table_("lanes", required=False)
    sub.finish()
    lanes = (
        parse_lanes(lanes_raw, path=f"{path}.lanes", source=source, findings=findings) if lanes_raw is not None else ()
    )
    isolation = (
        parse_isolation(isolation_raw, path=f"{path}.isolation", source=source, findings=findings)
        if isolation_raw is not None
        else None
    )

    def bad(message: str, fix: str) -> None:
        findings.append(Finding(rule="GATE-001", path=source, message=f"{path}: {message}", fix=fix))

    if not run:
        bad("'run' is empty", "set 'run' to the gate's argv, e.g. [\"python3\", \"ci/local_gate.py\"]")
        return None
    if mode not in MODES:
        bad(f"'mode' is {mode!r}", f"set 'mode' to one of {', '.join(MODES)}")
        mode = "enforce"
    mirrors: list[JobRef] = []
    for text in mirrors_raw:
        ref = JobRef.parse(text)
        if ref is None:
            bad(f"mirror {text!r} is not '<workflow file>:<job id>'", "write it as e.g. 'ci.yml:lint'")
        else:
            mirrors.append(ref)
    verify: JobRef | None = None
    if verify_raw is not None:
        verify = JobRef.parse(verify_raw)
        if verify is None:
            bad(f"verify {verify_raw!r} is not '<workflow file>:<job id>'", "write it as e.g. 'ci.yml:ci-mode'")
    return GateConfig(
        run=run,
        mirrors=tuple(mirrors),
        setup_steps=frozenset(setup_steps),
        verify=verify,
        verify_exempt=frozenset(verify_exempt),
        exempt_authors=frozenset(exempt_authors),
        mode=mode,
        source=source,
        isolation=isolation,
        lanes=lanes,
    )


@dataclass(frozen=True)
class GateLoad:
    """`config` is None when the repository has not opted in (or the
    declaration is unusable); `findings` are the declaration's own problems."""

    config: GateConfig | None
    findings: list[Finding]


@dataclass(frozen=True)
class _GateTable:
    raw: dict[str, TomlValue]  # the TOML wire boundary, parsed by parse_gate_table
    path: str
    source: str


def load_gate_config(repo_root: Path) -> GateLoad:
    """`ci.toml`'s `[local.gate]` wins; else `local-gate.toml`'s `[gate]`;
    else no config -- the repository has not opted in. Declaring both is a
    GATE-001 violation: two declarations can drift apart."""

    findings: list[Finding] = []
    tables: list[_GateTable] = []
    ci_toml = repo_root / "ci.toml"
    if ci_toml.is_file():
        try:
            doc = tomllib.loads(ci_toml.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            doc = {}  # load_ci_toml reports the parse error itself
        local = doc.get("local")
        gate = local.get("gate") if isinstance(local, dict) else None
        if isinstance(gate, dict):
            tables.append(_GateTable(gate, "local.gate", "ci.toml"))
    gate_file = repo_root / GATE_FILE
    if gate_file.is_file():
        try:
            doc = tomllib.loads(gate_file.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            return GateLoad(
                None,
                [Finding(rule="GATE-001", path=GATE_FILE, message=f"invalid TOML: {exc}", fix="fix the TOML syntax")],
            )
        top = Cursor(doc, "", findings, GATE_FILE)
        gate = top.table_("gate")
        top.finish()
        if gate is not None:
            tables.append(_GateTable(gate, "gate", GATE_FILE))
    if not tables:
        return GateLoad(None, findings)
    if len(tables) > 1:
        findings.append(
            Finding(
                rule="GATE-001",
                path=GATE_FILE,
                message="the local gate is declared in both ci.toml [local.gate] and local-gate.toml",
                fix="keep one declaration: ci.toml's [local.gate] when the repository has a ci.toml",
            )
        )
    first = tables[0]
    config = parse_gate_table(first.raw, path=first.path, source=first.source, findings=findings)
    return GateLoad(config, findings)


# ── git plumbing ─────────────────────────────────────────────────────────────


class GitError(Exception):
    pass


def _git(repo: Path, *args: str, stdin: str | None = None) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], input=stdin, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def tree_of(repo: Path, sha: str) -> str:
    return _git(repo, "rev-parse", f"{sha}^{{tree}}").strip()


def tracked_changes(repo: Path) -> list[str]:
    out = _git(repo, "status", "--porcelain", "--untracked-files=no")
    return [line for line in out.splitlines() if line.strip()]


@dataclass(frozen=True)
class Attestation:
    tree: str
    secs: int | None
    # GATE-007 provenance: `lint:run,rust:reused@<key12>,...`; None for an
    # unlaned gate.
    lanes: str | None = None

    def trailer(self) -> str:
        secs = f" secs={self.secs}" if self.secs is not None else ""
        lanes = f" lanes={self.lanes}" if self.lanes else ""
        return f"{TRAILER_KEY}: {TRAILER_VERSION} tree={self.tree}{secs}{lanes}"


def parse_attestation(message: str) -> Attestation | None:
    """The last `Local-Gate:` trailer in a commit message, or None."""

    matches = list(_TRAILER_RE.finditer(message))
    if not matches:
        return None
    fields: dict[str, str] = {}
    tokens = matches[-1].group("body").split()
    if not tokens or tokens[0] != TRAILER_VERSION:
        return None
    for token in tokens[1:]:
        key, sep, value = token.partition("=")
        if sep:
            fields[key] = value
    tree = fields.get("tree", "")
    if not _SHA40.match(tree):
        return None
    secs_text = fields.get("secs", "")
    return Attestation(tree=tree, secs=int(secs_text) if secs_text.isdigit() else None, lanes=fields.get("lanes") or None)


@dataclass(frozen=True)
class CommitCheck:
    """`state`: `attested` (trailer tree == commit tree), `attested-parent`
    (an unattested merge commit whose first parent is attested -- GitHub's
    "Update branch" button), `stale` (trailer present, tree differs: content
    changed after the gate ran), or `missing`."""

    sha: str
    state: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.state in ("attested", "attested-parent")


def check_commit(repo: Path, sha: str, *, _depth: int = 0) -> CommitCheck:
    try:
        message = _git(repo, "log", "-1", "--format=%B", sha)
        tree = tree_of(repo, sha)
        parents = _git(repo, "log", "-1", "--format=%P", sha).split()
    except GitError as exc:
        return CommitCheck(sha, "missing", f"cannot read commit {sha}: {exc}")
    att = parse_attestation(message)
    if att is not None:
        if att.tree == tree:
            return CommitCheck(sha, "attested", f"{TRAILER_KEY} trailer matches tree {tree[:12]}")
        return CommitCheck(
            sha, "stale", f"{TRAILER_KEY} trailer names tree {att.tree[:12]} but the commit's tree is {tree[:12]}"
        )
    if len(parents) >= 2 and _depth == 0:
        first = check_commit(repo, parents[0], _depth=1)
        if first.state == "attested":
            return CommitCheck(sha, "attested-parent", f"merge commit; first parent {parents[0][:12]} is attested")
    return CommitCheck(sha, "missing", f"no {TRAILER_KEY} trailer on {sha[:12]}")


def stamp_head(repo: Path, attestation: Attestation) -> str:
    """Rewrite HEAD's message with the trailer (replacing any older one) and
    return the new HEAD sha. The tree is untouched; `--no-verify` skips
    commit hooks because nothing they inspect (the tree) has changed."""

    message = _git(repo, "log", "-1", "--format=%B", "HEAD")
    new_message = _git(
        repo,
        "interpret-trailers",
        "--if-exists",
        "replace",
        "--trailer",
        attestation.trailer(),
        stdin=message,
    )
    _git(repo, "commit", "--amend", "--allow-empty", "--no-verify", "--quiet", "-F", "-", stdin=new_message)
    return _git(repo, "rev-parse", "HEAD").strip()


# ── local-gate run ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunOutcome:
    exit_code: int
    message: str
    head: str | None = None


def _changed(repo: Path, head: str) -> str | None:
    """None when the tree is untouched and HEAD did not move, else a message."""

    try:
        after = tracked_changes(repo)
        head_after = _git(repo, "rev-parse", "HEAD").strip()
    except GitError as exc:
        return str(exc)
    if after or head_after != head:
        return (
            "the gate passed but changed the repository (a formatter rewrote files, or HEAD moved); "
            "review and commit the result, then run the gate again:\n  " + "\n  ".join(after[:20])
        )
    return None


@dataclass(frozen=True)
class LaneRun:
    """The outcome of `run_lanes`: `provenance` is one `<lane>:run` or
    `<lane>:reused@<key12>` item per lane that passed, in order; `spent_secs`
    counts only lanes that actually ran."""

    exit_code: int
    provenance: list[str]
    spent_secs: int


def run_lanes(repo: Path, config: GateConfig, head: str, tree: str, *, use_cache: bool) -> LaneRun:
    """Run (or reuse) every declared lane in order (GATE-007)."""

    entries = tree_entries(repo, tree)
    versions = ToolVersions()
    provenance: list[str] = []
    spent = 0
    for lane in config.lanes:
        key = lane_key(entries, lane, gate_run=config.run, gate_source=config.source, versions=versions)
        hit = lookup(repo, lane, key.key) if use_cache else None
        if hit is not None:
            print(
                f"local-gate: lane {lane.id}: reused (inputs unchanged since a pass {hit.age_hours(time.time()):.1f} h ago "
                f"that took {hit.secs}s; key {key.key[:12]}, {key.inputs} inputs)",
                file=sys.stderr,
                flush=True,
            )
            provenance.append(f"{lane.id}:reused@{key.key[:12]}")
            continue
        print(f"local-gate: lane {lane.id}: {shlex.join(lane.run)}", file=sys.stderr, flush=True)
        start = time.monotonic()
        proc = subprocess.run(list(lane.run), cwd=repo, check=False)
        secs = int(round(time.monotonic() - start))
        spent += secs
        if proc.returncode != 0:
            print(f"local-gate: lane {lane.id}: FAILED after {secs}s (exit {proc.returncode})", file=sys.stderr)
            return LaneRun(proc.returncode or 1, provenance, spent)
        problem = _changed(repo, head)
        if problem is not None:
            print(f"local-gate: lane {lane.id}: {problem}", file=sys.stderr)
            return LaneRun(1, provenance, spent)
        record(repo, lane, key.key, secs=secs, head=head, tree=tree)
        print(f"local-gate: lane {lane.id}: passed in {secs}s", file=sys.stderr, flush=True)
        provenance.append(f"{lane.id}:run")
    return LaneRun(0, provenance, spent)


def run_gate(
    repo: Path, config: GateConfig, *, stamp: bool = True, force: bool = False, use_cache: bool = True
) -> RunOutcome:
    try:
        dirty = tracked_changes(repo)
        head = _git(repo, "rev-parse", "HEAD").strip()
    except GitError as exc:
        return RunOutcome(2, f"local-gate run: {exc}")
    if dirty:
        return RunOutcome(
            2,
            "local-gate run: tracked files have uncommitted changes; the gate attests a commit's tree, "
            "so commit (or stash) them first:\n  " + "\n  ".join(dirty[:20]),
        )
    if not force and check_commit(repo, head).state == "attested":
        return RunOutcome(0, f"local-gate run: HEAD {head[:12]} is already attested for its tree", head)
    tree = tree_of(repo, head)
    if config.lanes:
        start = time.monotonic()
        lane_run = run_lanes(repo, config, head, tree, use_cache=use_cache)
        secs = int(round(time.monotonic() - start))
        lanes_field = ",".join(lane_run.provenance)
        if lane_run.exit_code != 0:
            return RunOutcome(lane_run.exit_code, f"local-gate run: FAILED after {secs}s ({lanes_field or 'no lane passed'})")
        if not stamp:
            return RunOutcome(0, f"local-gate run: passed in {secs}s [{lanes_field}] (not stamped)", head)
        new_head = stamp_head(repo, Attestation(tree=tree, secs=secs, lanes=lanes_field))
        return RunOutcome(
            0, f"local-gate run: passed in {secs}s [{lanes_field}]; stamped {new_head[:12]} ({TRAILER_KEY} tree={tree[:12]})",
            new_head,
        )
    print(f"local-gate run: {config.command}", file=sys.stderr, flush=True)
    start = time.monotonic()
    proc = subprocess.run(list(config.run), cwd=repo, check=False)
    secs = int(round(time.monotonic() - start))
    if proc.returncode != 0:
        return RunOutcome(proc.returncode or 1, f"local-gate run: FAILED after {secs}s (exit {proc.returncode})")
    try:
        after = tracked_changes(repo)
        head_after = _git(repo, "rev-parse", "HEAD").strip()
    except GitError as exc:
        return RunOutcome(2, f"local-gate run: {exc}")
    if after or head_after != head:
        return RunOutcome(
            1,
            "local-gate run: the gate passed but changed the repository (a formatter rewrote files, or HEAD "
            "moved); review and commit the result, then run the gate again:\n  " + "\n  ".join(after[:20]),
        )
    if not stamp:
        return RunOutcome(0, f"local-gate run: passed in {secs}s (not stamped)", head)
    new_head = stamp_head(repo, Attestation(tree=tree, secs=secs))
    return RunOutcome(0, f"local-gate run: passed in {secs}s; stamped {new_head[:12]} ({TRAILER_KEY} tree={tree[:12]})", new_head)


# ── pre-push hook ────────────────────────────────────────────────────────────

ZERO_SHA = "0" * 40
HOOK_MARKER = "# installed by: ci-lint local-gate hook install (zackees/ci.yml#166)"


@dataclass(frozen=True)
class PushCheck:
    exit_code: int
    problems: list[str]


def check_push(repo: Path, stdin_text: str) -> PushCheck:
    """git's pre-push stdin: `<local ref> <local sha> <remote ref> <remote sha>`
    per line. Every pushed branch head must be attested; tags and deletes
    pass."""

    problems: list[str] = []
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, remote_ref, _remote_sha = parts
        if local_sha == ZERO_SHA or remote_ref.startswith("refs/tags/"):
            continue
        result = check_commit(repo, local_sha)
        if not result.ok:
            problems.append(f"{local_ref} -> {remote_ref}: {result.detail}")
    return PushCheck(exit_code=1 if problems else 0, problems=problems)


def hook_script(launcher: str) -> str:
    return f"#!/bin/sh\n{HOOK_MARKER}\nexec {launcher} local-gate check-push \"$@\"\n"


def default_launcher() -> str:
    package_parent = Path(__file__).resolve().parent.parent
    return f"env PYTHONPATH={shlex.quote(str(package_parent))} {shlex.quote(sys.executable)} -m ci_lint"


@dataclass(frozen=True)
class HookInstall:
    exit_code: int
    message: str


def install_hook(repo: Path, launcher: str, *, force: bool = False) -> HookInstall:
    try:
        hooks_dir = Path(_git(repo, "rev-parse", "--git-path", "hooks").strip())
    except GitError as exc:
        return HookInstall(2, f"local-gate hook install: {exc}")
    if not hooks_dir.is_absolute():
        hooks_dir = repo / hooks_dir
    hook = hooks_dir / "pre-push"
    if hook.exists() and HOOK_MARKER not in hook.read_text(encoding="utf-8", errors="replace") and not force:
        return HookInstall(
            1,
            f"local-gate hook install: {hook} already exists and was not installed by ci-lint; "
            "call `local-gate check-push` from it yourself, or pass --force to replace it",
        )
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook.write_text(hook_script(launcher), encoding="utf-8")
    hook.chmod(0o755)
    return HookInstall(0, f"local-gate hook install: wrote {hook}")


# ── CI-side verify (GATE-003) ────────────────────────────────────────────────


@dataclass(frozen=True)
class VerifyOutcome:
    exit_code: int
    attested: bool
    state: str
    message: str


def verify(
    repo: Path, config: GateConfig, *, sha: str, event: str, author: str | None
) -> VerifyOutcome:
    """Only `pull_request`/`pull_request_target` heads are judged: a push to
    the default branch is a merge of an already-verified PR, and schedule or
    dispatch runs are not an author's iteration loop."""

    if event not in ("pull_request", "pull_request_target"):
        return VerifyOutcome(0, False, "not-applicable", f"[GATE-003] event '{event}' is not a pull request; nothing to verify")
    if author is not None and author in config.exempt_authors:
        return VerifyOutcome(0, False, "exempt", f"[GATE-003] author '{author}' is exempt; the full remote run applies")
    result = check_commit(repo, sha)
    if result.ok:
        return VerifyOutcome(0, True, result.state, f"[GATE-003] {sha[:12]} {result.state}: {result.detail}")
    fix = (
        f"run the local gate and push its stamped commit: `ci-lint local-gate run` (runs {config.command}), "
        "then `git push --force-with-lease`"
    )
    message = f"[GATE-003] {sha[:12]} not attested ({result.state}): {result.detail}\n    fix:   {fix}"
    if config.mode == "shadow":
        return VerifyOutcome(0, False, result.state, message + "\n    (mode = shadow: reported, not enforced)")
    return VerifyOutcome(1, False, result.state, message)


# ── static rules (GATE-001, GATE-002) ────────────────────────────────────────


def _logical_lines(script: str) -> list[str]:
    joined = re.sub(r"\\\n\s*", " ", script)
    out: list[str] = []
    for raw in joined.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or _SHELL_BOILERPLATE.match(line):
            continue
        out.append(re.sub(r"\s+", " ", line))
    return out


def invokes_gate(line: str, config: GateConfig) -> bool:
    """A line invokes the gate when it *starts* with the gate argv (extra
    trailing arguments such as `--lane lint` are allowed), optionally after
    leading `VAR=value` environment assignments."""

    tokens = line.split()
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens = tokens[1:]
    return tuple(tokens[: len(config.run)]) == config.run


@dataclass
class _Workflows:
    docs: dict[str, dict[str, YamlValue]] = field(default_factory=dict)
    unreadable: set[str] = field(default_factory=set)


def _load_workflows(repo_root: Path) -> _Workflows:
    from ci_lint.workflow_scan import as_dict, load_workflows  # noqa: PLC0415 -- YAML tooling is optional
    from ci_lint.yaml_io import LoadStatus  # noqa: PLC0415

    out = _Workflows()
    for wf in load_workflows(repo_root):
        name = Path(wf.path).name
        if wf.status != LoadStatus.OK:
            out.unreadable.add(name)
        else:
            out.docs[name] = as_dict(wf.document)
    return out


def check_gate_static(config: GateConfig, repo_root: Path) -> list[Finding]:
    from ci_lint.workflow_scan import jobs_of, steps_of  # noqa: PLC0415

    findings: list[Finding] = check_isolation(config.isolation, config.run, repo_root, config.source)
    findings.extend(check_lanes_static(config.lanes, config.run, config.source, repo_root))
    wfs = _load_workflows(repo_root)
    if not wfs.docs and not wfs.unreadable and not config.mirrors and config.verify is None:
        # No remote CI at all (e.g. zackees/ci.yml itself): nothing to mirror
        # or verify; the pre-push hook is the whole enforcement.
        return findings

    def job_of(ref: JobRef, rule: str) -> dict[str, YamlValue] | None:
        path = f".github/workflows/{ref.workflow}"
        if ref.workflow in wfs.unreadable:
            findings.append(
                Finding(rule=rule, path=path, status=Status.NEEDS_REVIEW, message=f"cannot parse {ref.workflow}",
                        fix="install PyYAML or yq so the workflow can be checked")
            )
            return None
        doc = wfs.docs.get(ref.workflow)
        job = jobs_of(doc).get(ref.job) if doc is not None else None
        if job is None:
            findings.append(
                Finding(rule=rule, path=config.source, message=f"declared job '{ref}' does not exist",
                        fix=f"point the declaration at a real job, or add job '{ref.job}' to {path}")
            )
        return job

    # GATE-001: every mirrored remote job runs the gate and nothing else.
    if not config.mirrors:
        findings.append(
            Finding(
                rule="GATE-001",
                path=config.source,
                status=Status.NEEDS_REVIEW,
                message="no remote job is declared as a mirror of the local gate, so remote ⊆ local is unproven",
                fix="list the quick-gate job(s) in 'mirrors' and make their run steps invoke the gate command",
            )
        )
    for ref in config.mirrors:
        job = job_of(ref, "GATE-001")
        if job is None:
            continue
        path = f".github/workflows/{ref.workflow}"
        if "uses" in job:
            findings.append(
                Finding(rule="GATE-001", path=path, status=Status.NEEDS_REVIEW,
                        message=f"mirrored job '{ref.job}' is a reusable-workflow call; its steps cannot be checked here",
                        fix="mirror the called workflow's job instead")
            )
            continue
        invoked = False
        for index, step in enumerate(steps_of(job)):
            name = step.get("name")
            label = name if isinstance(name, str) else f"step {index + 1}"
            if isinstance(name, str) and name in config.setup_steps:
                continue
            uses = step.get("uses")
            if isinstance(uses, str):
                slug = uses.split("@", 1)[0]
                if slug not in SETUP_ACTIONS:
                    findings.append(
                        Finding(
                            rule="GATE-001",
                            path=path,
                            message=f"mirrored job '{ref.job}' step '{label}' uses '{slug}', which may run a remote-only check",
                            fix=f"move the check into the gate ({config.command}), or list the step name in 'setup-steps' if it only prepares the environment",
                        )
                    )
                continue
            run = step.get("run")
            if not isinstance(run, str):
                continue
            for line in _logical_lines(run):
                if invokes_gate(line, config):
                    invoked = True
                    continue
                findings.append(
                    Finding(
                        rule="GATE-001",
                        path=path,
                        message=f"mirrored job '{ref.job}' step '{label}' runs `{line[:120]}`, which the local gate does not",
                        fix=f"move that command into the gate ({config.command}) and have this step invoke the gate, "
                        "or list the step in 'setup-steps' if it only prepares the environment",
                    )
                )
        if not invoked:
            findings.append(
                Finding(rule="GATE-001", path=path,
                        message=f"mirrored job '{ref.job}' never invokes the gate command `{config.command}`",
                        fix="add a step that runs the gate command (lane arguments may follow it)")
            )

    # GATE-002: the PR entry verifies the attestation before anything else runs.
    if config.verify is None:
        findings.append(
            Finding(rule="GATE-002", path=config.source,
                    message="no 'verify' job is declared, so an unattested PR head still gets the full remote run",
                    fix="declare verify = '<workflow>:<job>' and run `ci-lint local-gate verify` in that job")
        )
        return findings
    ref = config.verify
    job = job_of(ref, "GATE-002")
    if job is None:
        return findings
    path = f".github/workflows/{ref.workflow}"
    runs = [s.get("run") for s in steps_of(job)]
    if not any(isinstance(r, str) and VERIFY_TOKEN in r for r in runs):
        findings.append(
            Finding(rule="GATE-002", path=path,
                    message=f"verify job '{ref.job}' has no step running `ci-lint {VERIFY_TOKEN}`",
                    fix=f"add a step: python3 -m ci_lint {VERIFY_TOKEN} --repo .")
        )
    if "if" in job:
        findings.append(
            Finding(rule="GATE-002", path=path, status=Status.NEEDS_REVIEW,
                    message=f"verify job '{ref.job}' has an 'if:'; confirm it always runs on pull_request",
                    fix="drop the job-level 'if:' (verify itself skips non-PR events)")
        )
    jobs = jobs_of(wfs.docs[ref.workflow])

    def needs_of(job_id: str) -> list[str]:
        raw = jobs.get(job_id, {}).get("needs")
        if isinstance(raw, str):
            return [raw]
        return [n for n in raw if isinstance(n, str)] if isinstance(raw, list) else []

    def reaches(job_id: str) -> bool:
        seen: set[str] = set()
        stack = needs_of(job_id)
        while stack:
            dep = stack.pop()
            if dep == ref.job:
                return True
            if dep not in seen:
                seen.add(dep)
                stack.extend(needs_of(dep))
        return False

    for job_id in jobs:
        if job_id == ref.job or job_id in config.verify_exempt:
            continue
        if not reaches(job_id):
            findings.append(
                Finding(
                    rule="GATE-002",
                    path=path,
                    message=f"job '{job_id}' does not (transitively) need verify job '{ref.job}', so it spends runner time on an unattested head",
                    fix=f"add '{ref.job}' to its needs: (directly or through a job it already needs), or list it in 'verify-exempt' "
                    "if it must run first (e.g. ci-pre)",
                )
            )
    return findings


def hook_installed(repo: Path) -> bool:
    try:
        hooks_dir = Path(_git(repo, "rev-parse", "--git-path", "hooks").strip())
    except GitError:
        return False
    hook = (hooks_dir if hooks_dir.is_absolute() else repo / hooks_dir) / "pre-push"
    return hook.is_file() and HOOK_MARKER in hook.read_text(encoding="utf-8", errors="replace")
