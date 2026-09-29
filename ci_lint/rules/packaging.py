"""Group 8: packaging (PKG-003, PKG-004, PKG-005).

Soldr is the only PEP 517 backend, the wheel bundles the native CLI as the
`PATH` command, and a missing native extension must fail loudly.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

from ci_lint.finding import Finding
from ci_lint.repo_files import list_repo_files
from ci_lint.schema import CliBinary, CiToml

SOLDR_PIN_RE = re.compile(r"^soldr\s*(==|>=|~=|>)")


def _load_toml(path: Path) -> dict[str, object] | None:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def check_pkg_004(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    pyproject = _load_toml(repo_root / "pyproject.toml")
    if pyproject is None:
        return [
            Finding(
                rule="PKG-004",
                path="pyproject.toml",
                message="no pyproject.toml at repo root, or it is not valid TOML",
                fix='add pyproject.toml with [build-system] build-backend = "soldr" and requires = '
                '["soldr==<version>"]',
            )
        ]
    build_system = pyproject.get("build-system")
    build_system = build_system if isinstance(build_system, dict) else {}
    backend = build_system.get("build-backend")
    if backend != "soldr":
        findings.append(
            Finding(
                rule="PKG-004",
                path="pyproject.toml",
                message=f"[build-system].build-backend = {backend!r}, expected \"soldr\"",
                fix='set [build-system] build-backend = "soldr" in pyproject.toml',
            )
        )
    requires = build_system.get("requires")
    requires = requires if isinstance(requires, list) else []
    if not any(isinstance(r, str) and SOLDR_PIN_RE.match(r) for r in requires):
        findings.append(
            Finding(
                rule="PKG-004",
                path="pyproject.toml",
                message="[build-system].requires has no 'soldr' version requirement "
                "('soldr>=<floor>' by default, or an exact 'soldr==<version>' pin per "
                "zackees/ci.yml#18's RUST-013)",
                fix='add "soldr>=<floor>" (or, only with a recorded [[exceptions]] entry, '
                '"soldr==<version>") to [build-system].requires in pyproject.toml',
            )
        )
    if any(isinstance(r, str) and "maturin" in r.lower() for r in requires):
        findings.append(
            Finding(
                rule="PKG-004",
                path="pyproject.toml",
                message="[build-system].requires includes maturin directly",
                fix="remove maturin from [build-system].requires; soldr pulls its own pinned maturin",
            )
        )
    dep_groups = pyproject.get("dependency-groups")
    if isinstance(dep_groups, dict):
        for group_name, entries in dep_groups.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                name = entry if isinstance(entry, str) else (
                    entry.get("include-group") if isinstance(entry, dict) else None
                )
                if isinstance(name, str) and "maturin" in name.lower():
                    findings.append(
                        Finding(
                            rule="PKG-004",
                            path="pyproject.toml",
                            message=f"[dependency-groups].{group_name} includes maturin",
                            fix=f"remove maturin from [dependency-groups].{group_name}; soldr pulls "
                            "its own pinned maturin",
                        )
                    )
    uv_lock = _load_toml(repo_root / "uv.lock")
    if uv_lock is not None:
        packages = uv_lock.get("package")
        for pkg in packages if isinstance(packages, list) else []:
            if not isinstance(pkg, dict):
                continue
            pkg_name = pkg.get("name")
            if pkg_name == "soldr":
                continue
            deps = pkg.get("dependencies")
            dep_names = {
                d.get("name")
                for d in deps
                if isinstance(d, dict) and isinstance(d.get("name"), str)
            } if isinstance(deps, list) else set()
            if "maturin" in dep_names:
                findings.append(
                    Finding(
                        rule="PKG-004",
                        path="uv.lock",
                        message=f"uv.lock: package '{pkg_name}' depends on maturin directly",
                        fix=f"remove the direct maturin dependency from '{pkg_name}'; only soldr may "
                        "depend on maturin",
                    )
                )
    return findings


def _bundle_bin_matches(entry: object, cli: CliBinary) -> bool:
    """`[tool.soldr.pep517].bundle-bins` accepts two shapes (round-2A brief,
    defect 3): a plain string naming the crate (`"template-cli"`), or a
    table `{ bin = "<cli command name>", package = "<crate>" }` -- the
    shape soldr's own docs use and the template repo ships
    (`[{ bin = "template-cli", package = "template-cli" }]`). A table entry
    matches on `bin` == `[python].cli.name`; if it also carries `package`,
    that must equal `[python].cli.crate`."""

    if isinstance(entry, str):
        return entry == cli.crate
    if isinstance(entry, dict):
        if entry.get("bin") != cli.name:
            return False
        package = entry.get("package")
        return package is None or package == cli.crate
    return False


def check_pkg_003(ci: CiToml, repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    if ci.python is None:
        return findings
    pyproject = _load_toml(repo_root / "pyproject.toml")
    if pyproject is None:
        return findings  # already reported by PKG-004
    project = pyproject.get("project")
    project = project if isinstance(project, dict) else {}
    for key in ("scripts", "gui-scripts"):
        scripts = project.get(key)
        if isinstance(scripts, dict) and ci.python.cli.name in scripts:
            findings.append(
                Finding(
                    rule="PKG-003",
                    path="pyproject.toml",
                    message=f"[project.{key}] defines '{ci.python.cli.name}', shadowing the native "
                    "CLI the wheel bundles",
                    fix=f"remove '{ci.python.cli.name}' from [project.{key}]; the command on PATH "
                    "must be the bundled Rust binary, not a Python shim",
                )
            )
    tool = pyproject.get("tool")
    soldr_tool = tool.get("soldr") if isinstance(tool, dict) else None
    pep517 = soldr_tool.get("pep517") if isinstance(soldr_tool, dict) else None
    bundle_bins = pep517.get("bundle-bins") if isinstance(pep517, dict) else None
    bundle_bins = bundle_bins if isinstance(bundle_bins, list) else []
    if not any(_bundle_bin_matches(entry, ci.python.cli) for entry in bundle_bins):
        findings.append(
            Finding(
                rule="PKG-003",
                path="pyproject.toml",
                message=f"[tool.soldr.pep517].bundle-bins {bundle_bins} does not include "
                f"'{ci.python.cli.crate}' (as a plain string, or as a table with "
                f"bin = \"{ci.python.cli.name}\")",
                fix=f'add "{ci.python.cli.crate}" (or {{ bin = "{ci.python.cli.name}", package = '
                f'"{ci.python.cli.crate}" }}) to [tool.soldr.pep517].bundle-bins in pyproject.toml',
            )
        )
    return findings


def _catches_import_error(node: ast.Try) -> bool:
    for handler in node.handlers:
        if handler.type is None:
            return True
        if isinstance(handler.type, ast.Name) and handler.type.id == "ImportError":
            return True
        if isinstance(handler.type, ast.Tuple) and any(
            isinstance(e, ast.Name) and e.id == "ImportError" for e in handler.type.elts
        ):
            return True
    return False


def _imports_native(node: ast.Try) -> bool:
    for stmt in node.body:
        if isinstance(stmt, ast.ImportFrom):
            if stmt.module and "_native" in stmt.module:
                return True
            # `from . import _native` / `from .pkg import _native as x`: the
            # module lives in `names`, not `module`, when it's a bare name.
            if any("_native" in alias.name for alias in stmt.names):
                return True
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if "_native" in alias.name:
                    return True
    return False


def check_pkg_005(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    # git-tracked (or not-yet-ignored) files under src/ only (ci_lint.repo_files):
    # round-1A's `src_dir.rglob("*.py")` also walked gitignored build output
    # such as a stray `src/**/.venv/`, `src/**/__pycache__/` or vendored tree.
    py_files = [
        repo_root / rel for rel in list_repo_files(repo_root) if rel.startswith("src/") and rel.endswith(".py")
    ]
    for f in py_files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        rel = f.relative_to(repo_root).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try) and _imports_native(node) and _catches_import_error(node):
                findings.append(
                    Finding(
                        rule="PKG-005",
                        path=rel,
                        line=node.lineno,
                        message="try/except ImportError around the '._native' import silently masks "
                        "a missing native extension",
                        fix="remove the try/except ImportError around the ._native import; import it "
                        "unconditionally so a missing native module fails loudly",
                    )
                )
    return findings


def check_group8(ci: CiToml, repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(check_pkg_004(repo_root))
    findings.extend(check_pkg_003(ci, repo_root))
    findings.extend(check_pkg_005(repo_root))
    return findings
