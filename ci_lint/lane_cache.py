"""GATE-007: the local lane result cache (zackees/ci.yml#177).

A local gate split into lanes (`[gate.lanes.<id>]`) may skip a lane only
when everything the lane can read is byte-identical to a run of that same
lane that passed. "Everything the lane can read" is decided by exclusion,
never by inclusion:

- **Input set** = every path in the commit's tree, minus the lane's
  `exclude` globs, plus the lane's `keep` globs (re-included despite an
  exclusion), plus the *mandatory* inputs no exclusion can drop: the gate
  declaration file, every repository file named in the gate's or the
  lane's argv, lockfiles and toolchain pins. A forgotten exclusion costs a
  rerun; a forgotten include could silently skip required work, so
  include lists are not offered.
- **Key** = sha256 over a versioned header, the lane's argv, the versions
  of the tools the lane declares (`<tool> --version`), the values of its
  declared env vars, and `(path, blob sha)` for every input -- read from
  `git ls-tree`, so hashing costs one git call, not a file read.
- **Only passes are recorded**, under `<git common dir>/ci-lint/lane-cache/`
  (shared by every worktree of the clone, never pushed). An entry older
  than the lane's `max-age-hours` (default 24, GEN-021's freshness bound)
  is ignored, so environment drift (registries, toolchains, the network)
  cannot hide behind an old pass forever.
- **Provenance**: the gate records each lane as `run` or `reused@<key>` in
  the `Local-Gate:` trailer, so a reused lane is visible in the commit.

The cache is local only. CI's mirrored jobs always run (GATE-001); skipping
on the remote side is GEN-021's verified reuse, a different mechanism.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ci_lint.finding import Finding, Status
from ci_lint.globs import glob_to_regex
from ci_lint.proc import run_captured
from ci_lint.toml_cursor import Cursor, TomlValue

KEY_VERSION = "gate-007/v1"
DEFAULT_MAX_AGE_HOURS = 24.0
# Gate declarations: always a lane input, for every lane.
DECLARATION_BASENAMES: frozenset[str] = frozenset({"local-gate.toml", "ci.toml"})


@dataclass(frozen=True)
class EcosystemPins:
    """Lockfiles and toolchain pins a lane must treat as inputs when it
    drives any of `tools` (design revision 3, #177: soldr's Python-lint lane
    was invalidated by every Cargo.lock bump it cannot be affected by)."""

    tools: frozenset[str]
    basenames: frozenset[str]


ECOSYSTEM_PINS: tuple[EcosystemPins, ...] = (
    EcosystemPins(
        frozenset({"soldr", "cargo", "rustc", "rustup", "maturin"}),
        frozenset({"Cargo.lock", "rust-toolchain.toml", "rust-toolchain"}),
    ),
    EcosystemPins(
        frozenset({"uv", "uvx", "python", "python3", "pip", "poetry", "maturin"}),
        frozenset({"uv.lock", "poetry.lock", ".python-version"}),
    ),
    EcosystemPins(
        frozenset({"node", "npm", "npx", "pnpm", "yarn"}),
        frozenset({"package-lock.json", "pnpm-lock.yaml", "yarn.lock"}),
    ),
)
ALL_PIN_BASENAMES: frozenset[str] = frozenset().union(*(e.basenames for e in ECOSYSTEM_PINS))


def pin_basenames(tools: tuple[str, ...]) -> frozenset[str]:
    """The pins a lane's tools make mandatory. A lane that declares no
    recognized tool gets every pin: the safe default when the gate cannot
    tell what the lane resolves."""

    matched = [e.basenames for e in ECOSYSTEM_PINS if e.tools & set(tools)]
    if not matched:
        return ALL_PIN_BASENAMES
    return frozenset().union(*matched)


@dataclass(frozen=True)
class LaneConfig:
    id: str
    run: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    keep: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    env: tuple[str, ...] = ()
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS


def parse_lanes(raw: dict[str, TomlValue], *, path: str, source: str, findings: list[Finding]) -> tuple[LaneConfig, ...]:
    lanes: list[LaneConfig] = []
    for lane_id, body in raw.items():
        lane_path = f"{path}.{lane_id}"
        if not isinstance(body, dict):
            findings.append(
                Finding(rule="CT-002", path=source, message=f"'{lane_path}' must be a table",
                        fix=f"declare [{lane_path}] with run = [...]")
            )
            continue
        sub = Cursor(body, lane_path, findings, source)
        run = sub.list_str("run")
        exclude = sub.list_str("exclude", required=False)
        keep = sub.list_str("keep", required=False)
        tools = sub.list_str("tools", required=False)
        env = sub.list_str("env", required=False)
        max_age = sub.int_("max-age-hours", required=False)
        sub.finish()
        if not run:
            continue
        lanes.append(
            LaneConfig(
                id=lane_id,
                run=run,
                exclude=exclude,
                keep=keep,
                tools=tools,
                env=env,
                max_age_hours=float(max_age) if max_age is not None else DEFAULT_MAX_AGE_HOURS,
            )
        )
    return tuple(lanes)


# ── input set and key ────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    proc = run_captured(["git", "-C", str(repo), *args])
    if not proc.ok:
        raise subprocess.CalledProcessError(proc.returncode, ["git", *args], proc.stdout, proc.stderr)
    return proc.stdout


@dataclass(frozen=True, order=True)
class TreeEntry:
    """One path in a commit's tree and the sha of its blob (or gitlink)."""

    path: str
    sha: str


def tree_entries(repo: Path, tree: str) -> list[TreeEntry]:
    """Every blob/gitlink in `tree`, sorted by path."""

    out: list[TreeEntry] = []
    for line in _git(repo, "ls-tree", "-r", "-z", "--full-tree", tree).split("\0"):
        if not line:
            continue
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) == 3:
            out.append(TreeEntry(path=path, sha=parts[2]))
    return sorted(out)


def mandatory_paths(lane: LaneConfig, gate_run: tuple[str, ...], gate_source: str, paths: set[str]) -> set[str]:
    named = {token for token in (*gate_run, *lane.run) if token in paths}
    named.add(gate_source)
    always = DECLARATION_BASENAMES | pin_basenames(lane.tools)
    named.update(p for p in paths if p.rsplit("/", 1)[-1] in always)
    return named & paths


@dataclass
class _Matcher:
    exclude: list[re.Pattern[str]]
    keep: list[re.Pattern[str]]

    @staticmethod
    def of(lane: LaneConfig) -> _Matcher:
        return _Matcher([glob_to_regex(p) for p in lane.exclude], [glob_to_regex(p) for p in lane.keep])

    def excluded(self, path: str) -> bool:
        return any(r.match(path) for r in self.exclude) and not any(r.match(path) for r in self.keep)


def lane_inputs(
    entries: list[TreeEntry], lane: LaneConfig, gate_run: tuple[str, ...], gate_source: str
) -> list[TreeEntry]:
    mandatory = mandatory_paths(lane, gate_run, gate_source, {e.path for e in entries})
    matcher = _Matcher.of(lane)
    return [e for e in entries if e.path in mandatory or not matcher.excluded(e.path)]


@dataclass(frozen=True)
class ToolVersion:
    tool: str
    version: str


@dataclass
class ToolVersions:
    """`<tool> --version`, memoized per gate run. A missing tool records
    `missing`, so installing it later changes the key."""

    known: list[ToolVersion] = field(default_factory=list)

    def get(self, tool: str) -> ToolVersion:
        for item in self.known:
            if item.tool == tool:
                return item
        exe = shutil.which(tool)
        if exe is None:
            version = "missing"
        else:
            proc = run_captured([exe, "--version"], timeout=60)
            text = (proc.stdout + proc.stderr).strip()
            # soldr prints a delegation notice before its version; keep version-shaped lines only.
            lines = [ln for ln in text.splitlines() if re.search(r"\d+\.\d+", ln) and "delegating" not in ln]
            version = (lines[-1] if lines else text).strip()
        item = ToolVersion(tool=tool, version=version)
        self.known.append(item)
        return item


@dataclass(frozen=True)
class EnvValue:
    name: str
    value: str


@dataclass(frozen=True)
class KeyHeader:
    """Everything but the input files that a lane's key covers; serialized
    to JSON only at the hashing boundary."""

    v: str
    run: tuple[str, ...]
    tools: tuple[ToolVersion, ...]
    env: tuple[EnvValue, ...]


@dataclass(frozen=True)
class LaneKey:
    key: str
    inputs: int
    tools: tuple[ToolVersion, ...]


def lane_key(
    entries: list[TreeEntry],
    lane: LaneConfig,
    *,
    gate_run: tuple[str, ...],
    gate_source: str,
    versions: ToolVersions,
    environ: Mapping[str, str] | None = None,
) -> LaneKey:
    env = os.environ if environ is None else environ
    inputs = lane_inputs(entries, lane, gate_run, gate_source)
    header = KeyHeader(
        v=KEY_VERSION,
        run=lane.run,
        tools=tuple(versions.get(tool) for tool in sorted(set(lane.tools))),
        env=tuple(EnvValue(name, env.get(name, "")) for name in sorted(set(lane.env))),
    )
    h = hashlib.sha256(json.dumps(asdict(header), sort_keys=True).encode())
    for entry in inputs:
        h.update(b"\0")
        h.update(entry.path.encode())
        h.update(b"\0")
        h.update(entry.sha.encode())
    return LaneKey(key=h.hexdigest(), inputs=len(inputs), tools=header.tools)


# ── the cache itself ─────────────────────────────────────────────────────────


def cache_dir(repo: Path) -> Path:
    common = Path(_git(repo, "rev-parse", "--git-common-dir").strip())
    if not common.is_absolute():
        common = repo / common
    return common / "ci-lint" / "lane-cache"


@dataclass(frozen=True)
class CacheEntry:
    lane: str
    key: str
    passed_at: float
    secs: int
    head: str
    tree: str

    def age_hours(self, now: float) -> float:
        return (now - self.passed_at) / 3600.0


def lookup(repo: Path, lane: LaneConfig, key: str, *, now: float | None = None) -> CacheEntry | None:
    path = cache_dir(repo) / lane.id / f"{key}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("key") != key or raw.get("lane") != lane.id:
        return None
    passed_at = raw.get("passed_at")
    secs = raw.get("secs")
    if not isinstance(passed_at, (int, float)) or not isinstance(secs, int):
        return None
    entry = CacheEntry(lane.id, key, float(passed_at), secs, str(raw.get("head", "")), str(raw.get("tree", "")))
    current = time.time() if now is None else now
    if entry.age_hours(current) > lane.max_age_hours or entry.passed_at > current + 60:
        return None
    return entry


def record(repo: Path, lane: LaneConfig, key: str, *, secs: int, head: str, tree: str) -> Path:
    directory = cache_dir(repo) / lane.id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{key}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"lane": lane.id, "key": key, "passed_at": time.time(), "secs": secs, "head": head, "tree": tree}),
        encoding="utf-8",
    )
    tmp.replace(path)
    prune(directory, keep=64)
    return path


def prune(directory: Path, *, keep: int) -> None:
    """Keep the newest `keep` entries per lane; the rest can never be
    looked up again in practice (a key only recurs for an identical tree)."""

    entries = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in entries[keep:]:
        stale.unlink(missing_ok=True)


# ── static checks (GATE-007) ─────────────────────────────────────────────────


def check_lanes_static(
    lanes: tuple[LaneConfig, ...], gate_run: tuple[str, ...], gate_source: str, repo_root: Path
) -> list[Finding]:
    findings: list[Finding] = []
    if not lanes:
        return findings
    try:
        entries = tree_entries(repo_root, "HEAD")
    except subprocess.CalledProcessError:
        return [Finding(rule="GATE-007", path=gate_source, status=Status.NEEDS_REVIEW,
                        message="cannot list HEAD's tree to check lane exclusions", fix="run inside a git checkout")]
    paths = {e.path for e in entries}
    for lane in lanes:
        if tuple(lane.run[: len(gate_run)]) != gate_run:
            findings.append(
                Finding(
                    rule="GATE-007",
                    path=gate_source,
                    message=f"lane '{lane.id}' runs `{' '.join(lane.run)}`, which is not the gate command "
                    f"`{' '.join(gate_run)}` plus arguments",
                    fix="make every lane a slice of the one gate command (e.g. `<gate> --lane <id>`), so the union "
                    "of the lanes is the gate the remote jobs mirror",
                )
            )
        # Mandatory inputs are re-included at runtime whatever the globs say, so
        # a broad glob that happens to cover one (`**/*.py` over the gate
        # script) is harmless. An exclusion that *names* one is a statement of
        # intent to drop it, and that is what gets reported.
        mandatory = mandatory_paths(lane, gate_run, gate_source, paths)
        dropped = sorted(p for p in lane.exclude if not any(c in p for c in "*?[") and p in mandatory)
        if dropped:
            findings.append(
                Finding(
                    rule="GATE-007",
                    path=gate_source,
                    message=f"lane '{lane.id}' excludes mandatory inputs: {', '.join(dropped[:8])}",
                    fix="narrow the exclusion (or add a `keep` glob): the gate script, its declaration, lockfiles "
                    "and toolchain pins are always lane inputs",
                )
            )
        if not lane.tools:
            findings.append(
                Finding(
                    rule="GATE-007",
                    path=gate_source,
                    status=Status.NEEDS_REVIEW,
                    message=f"lane '{lane.id}' declares no `tools`, so a tool upgrade cannot invalidate its cache",
                    fix="list the executables the lane drives (e.g. tools = [\"soldr\", \"uv\"])",
                )
            )
    ids = [lane.id for lane in lanes]
    if len(set(ids)) != len(ids):
        findings.append(Finding(rule="GATE-007", path=gate_source, message="duplicate lane ids", fix="give each lane a unique id"))
    return findings


# ── history simulation (tuning exclusions from real commits) ─────────────────


@dataclass(frozen=True)
class LaneSimulation:
    lane: str
    commits: int
    reusable: int
    # The paths that most often forced this lane to run, most frequent first.
    top_triggers: tuple[str, ...]

    @property
    def rate(self) -> float:
        return self.reusable / self.commits if self.commits else 0.0


def simulate(
    repo: Path, lanes: tuple[LaneConfig, ...], gate_run: tuple[str, ...], gate_source: str, *, commits: int
) -> list[LaneSimulation]:
    """For each of the last `commits` first-parent commits of HEAD, would
    each lane's inputs have been unchanged from the commit's parent? That is
    the best case for the cache (a pass of the parent recorded), which makes
    it the right signal for tuning `exclude`: a lane that is rarely reusable
    either reads everything or excludes too little."""

    shas = _git(repo, "rev-list", "--first-parent", f"--max-count={commits}", "HEAD").split()
    paths = {e.path for e in tree_entries(repo, "HEAD")}
    reusable = {lane.id: 0 for lane in lanes}
    triggers: dict[str, dict[str, int]] = {lane.id: {} for lane in lanes}
    counted = 0
    for sha in shas:
        try:
            changed = [p for p in _git(repo, "diff", "--name-only", f"{sha}^", sha).splitlines() if p]
        except subprocess.CalledProcessError:
            continue  # root commit
        counted += 1
        for lane in lanes:
            matcher = _Matcher.of(lane)
            mandatory = mandatory_paths(lane, gate_run, gate_source, paths | set(changed))
            hits = [p for p in changed if p in mandatory or not matcher.excluded(p)]
            if not hits:
                reusable[lane.id] += 1
            for p in hits:
                top = p.split("/", 2)
                bucket = "/".join(top[:2]) if len(top) > 2 else p
                triggers[lane.id][bucket] = triggers[lane.id].get(bucket, 0) + 1
    out: list[LaneSimulation] = []
    for lane in lanes:
        ranked = sorted(triggers[lane.id].items(), key=lambda kv: -kv[1])[:5]
        out.append(LaneSimulation(lane.id, counted, reusable[lane.id], tuple(f"{p} ({n})" for p, n in ranked)))
    return out



# ── exclusion audit (design revision 4, #177) ────────────────────────────────


@dataclass(frozen=True)
class TraceAudit:
    """Tracked files a lane opened while running under `strace`, and those
    of them its exclusions would have dropped (each one an unsafe
    exclusion: the lane's result can depend on a file its key ignores)."""

    lane: str
    opened: int
    excluded_reads: tuple[str, ...]


_TRACE_PATH = re.compile(r'"(/[^"]+)"')


def audit_trace(
    repo_root: Path, lane: LaneConfig, gate_run: tuple[str, ...], gate_source: str, trace_text: str
) -> TraceAudit:
    """Only successful opens/execs of *tracked* files count. Reads made by
    processes outside the traced tree -- a build daemon, a container -- are
    invisible here, so a clean audit proves nothing about them."""

    root = str(repo_root.resolve()).rstrip("/") + "/"
    tracked = {e.path for e in tree_entries(repo_root, "HEAD")}
    opened: set[str] = set()
    for line in trace_text.splitlines():
        if " = -1 " in line:
            continue
        for path in _TRACE_PATH.findall(line):
            if path.startswith(root):
                rel = path[len(root):]
                if rel in tracked:
                    opened.add(rel)
    mandatory = mandatory_paths(lane, gate_run, gate_source, tracked)
    matcher = _Matcher.of(lane)
    bad = tuple(sorted(p for p in opened if p not in mandatory and matcher.excluded(p)))
    return TraceAudit(lane=lane.id, opened=len(opened), excluded_reads=bad)


def run_audit(repo_root: Path, lane: LaneConfig, gate_run: tuple[str, ...], gate_source: str) -> TraceAudit | None:
    """Run the lane under `strace -f` and audit it; None when strace is absent."""

    strace = shutil.which("strace")
    if strace is None:
        return None
    trace_file = cache_dir(repo_root).parent / f"audit-{lane.id}.trace"
    trace_file.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [strace, "-f", "-qq", "-e", "trace=openat,open,execve,stat,lstat,newfstatat,statx", "-o", str(trace_file), *lane.run],
        cwd=repo_root,
        check=False,
    )
    try:
        text = trace_file.read_text(encoding="utf-8", errors="replace")
    finally:
        trace_file.unlink(missing_ok=True)
    return audit_trace(repo_root, lane, gate_run, gate_source, text)
