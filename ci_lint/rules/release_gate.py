"""Group 11: release-gate rules (zackees/ci.yml#8 -- lessons from the
mimalloc-pprof v1.0.1 exact-SHA release pilot, four latent publisher
bugs that only showed up on the real (non-dry) publish path).

REL-001: a `ci/**/*.py` (or root `*.py`) script that runs `gh ... --json`
(or otherwise parses `gh`'s stdout) must not do so with an unmodified
`os.environ` -- `setup-soldr` exports `CLICOLOR_FORCE=1`/`FORCE_COLOR=1`
through `GITHUB_ENV` to every later step, which makes `gh --json` print
pretty, ANSI-coloured JSON instead of the plain JSON its own docs promise,
breaking `json.loads`/ndjson parsing (mimalloc-pprof#560, reproduced
locally: 5 lines, leading `\x1b[1;37m{`). The static check is a
per-call-site heuristic, not full dataflow: a `subprocess.run`/
`subprocess.check_output`/`subprocess.check_call` call whose argv starts
with `gh` and includes `--json` (or `-q`, the same parsed-output family)
is flagged unless that same call passes an `env=` keyword -- the fix is
always to build that `env=` from a stripped `os.environ` copy, never to
delete the finding by adding a bare `env=os.environ`.

REL-002: a Rust repo (a `Cargo.toml` with a `[workspace]` table) that uses
`zackees/setup-soldr` must `.gitignore` the cook-cache side files it
restores BESIDE `target/` (`target.soldr-base.tar.zst`,
`target.soldr-delta.tar.zst`, `target.soldr-base-manifest.pb`) -- a bare
`target` gitignore entry matches only a path/dir named exactly `target`,
not a sibling file whose name starts with `target.`. Without this, a
release-candidate "checkout must be clean" assertion fails on a clean
release with nothing wrong (mimalloc-pprof#561,
run 36463019197).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from ci_lint.finding import Finding, Status
from ci_lint.repo_files import list_repo_files
from ci_lint.schema import CiToml

GH_JSON_FLAGS: frozenset[str] = frozenset({"--json", "-q"})
SOLDR_SIDE_FILE_GLOB = "target.soldr-*"
SOLDR_SIDE_FILE_EXAMPLES: tuple[str, ...] = (
    "target.soldr-base.tar.zst",
    "target.soldr-delta.tar.zst",
    "target.soldr-base-manifest.pb",
)

REL001_FIX = (
    "pass env= to this subprocess call, built from a stripped os.environ that drops "
    "CLICOLOR_FORCE, FORCE_COLOR and GH_FORCE_TTY (and sets NO_COLOR=1), e.g. "
    "env={k: v for k, v in os.environ.items() if k not in "
    "('CLICOLOR_FORCE', 'FORCE_COLOR', 'GH_FORCE_TTY')} | {'NO_COLOR': '1'} -- "
    "setup-soldr exports CLICOLOR_FORCE/FORCE_COLOR through GITHUB_ENV to every later step, "
    "which makes 'gh --json' print pretty ANSI-coloured JSON instead of plain JSON "
    "(zackees/ci.yml#8, mimalloc-pprof#560)"
)


def _literal_str(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _argv_from_call(call: ast.Call) -> list[str | None] | None:
    """Best-effort argv from the call's first positional arg (a list/tuple
    literal). A non-literal element (e.g. an f-string tag/variable) is kept
    as `None` rather than aborting the whole scan -- only argv[0] (the
    program name) and the presence of a literal '--json'/'-q' flag need to
    be known for real; the rest of the argv can stay opaque."""

    if not call.args:
        return None
    first = call.args[0]
    if not isinstance(first, (ast.List, ast.Tuple)):
        return None
    return [_literal_str(elt) for elt in first.elts]


def _has_env_kwarg(call: ast.Call) -> bool:
    return any(kw.arg == "env" for kw in call.keywords)


SUBPROCESS_FUNCS = frozenset({"run", "check_output", "check_call", "Popen"})


def _is_subprocess_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in SUBPROCESS_FUNCS:
        if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
            return True
    if isinstance(func, ast.Name) and func.id in SUBPROCESS_FUNCS:
        return True
    return False


def _scan_file_for_rel001(path: Path, repo_rel: str) -> list[Finding]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_subprocess_call(node):
            continue
        argv = _argv_from_call(node)
        if not argv:
            continue
        if argv[0] != "gh":
            continue
        literal_tail = {tok for tok in argv[1:] if tok is not None}
        if not (GH_JSON_FLAGS & literal_tail):
            continue
        if _has_env_kwarg(node):
            continue
        rendered_argv = " ".join(tok if tok is not None else "<expr>" for tok in argv[1:])
        findings.append(
            Finding(
                rule="REL-001",
                path=repo_rel,
                line=getattr(node, "lineno", None),
                message=f"{repo_rel}:{getattr(node, 'lineno', '?')}: 'gh {rendered_argv}' is parsed "
                "for its JSON output but this subprocess call has no env= -- a step that ran after "
                "setup-soldr will have CLICOLOR_FORCE/FORCE_COLOR set, and 'gh --json' then prints "
                "ANSI-coloured pretty JSON that json.loads/ndjson parsing cannot read",
                fix=REL001_FIX,
            )
        )
    return findings


def check_rel_001(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for rel_raw in list_repo_files(repo_root):
        rel = Path(rel_raw)
        if rel.suffix != ".py":
            continue
        is_root_script = rel.parent == Path(".")
        is_ci_script = rel.parts and rel.parts[0] == "ci"
        if not (is_root_script or is_ci_script):
            continue
        findings.extend(_scan_file_for_rel001(repo_root / rel, str(rel)))
    return findings


def _repo_uses_setup_soldr(repo_root: Path) -> bool:
    for wf_dir in ("workflows", "actions"):
        base = repo_root / ".github" / wf_dir
        if not base.is_dir():
            continue
        for path in base.rglob("*.y*ml"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "setup-soldr" in text:
                return True
    return False


def _is_rust_repo(repo_root: Path) -> bool:
    cargo_toml = repo_root / "Cargo.toml"
    if not cargo_toml.is_file():
        return False
    try:
        text = cargo_toml.read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(r"^\[workspace\]", text, re.MULTILINE) is not None


def _gitignore_covers_soldr_side_files(repo_root: Path) -> bool:
    gitignore = repo_root / ".gitignore"
    if not gitignore.is_file():
        return False
    try:
        lines = gitignore.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for raw in lines:
        pattern = raw.strip()
        if not pattern or pattern.startswith("#"):
            continue
        pattern = pattern.rstrip("/")
        if pattern in ("target.soldr-*", "target.soldr*", "target.*"):
            return True
        if pattern.startswith("**/") and pattern[3:] in ("target.soldr-*", "target.soldr*"):
            return True
    return False


def check_rel_002(repo_root: Path) -> list[Finding]:
    if not _is_rust_repo(repo_root):
        return []
    if not _repo_uses_setup_soldr(repo_root):
        return []
    if _gitignore_covers_soldr_side_files(repo_root):
        return []
    return [
        Finding(
            rule="REL-002",
            status=Status.VIOLATION,
            path=".gitignore",
            message="this Rust repo uses zackees/setup-soldr but .gitignore does not cover the cook-cache "
            f"side files it restores beside target/ (e.g. {', '.join(SOLDR_SIDE_FILE_EXAMPLES)}) -- a bare "
            "'target' entry matches only a path named exactly 'target', not a sibling file starting with "
            "'target.'",
            fix=f"add '{SOLDR_SIDE_FILE_GLOB}' to .gitignore so a release-candidate clean-tree assertion "
            "does not fail on setup-soldr's own restored cache side files (zackees/ci.yml#8, "
            "mimalloc-pprof#561)",
        )
    ]


def check_group11(ci: CiToml, repo_root: Path) -> list[Finding]:
    del ci  # unused -- these checks scan Python sources / .gitignore directly
    findings: list[Finding] = []
    findings.extend(check_rel_001(repo_root))
    findings.extend(check_rel_002(repo_root))
    return findings
