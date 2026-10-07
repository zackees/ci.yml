"""Discover and parse workflow / composite-action YAML files.

Shared by rule groups 2-5 (workflows, shell budget, tools, secrets), so the
YAML-fallback-order concern (`ci_lint.yaml_io`) lives in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import tempfile
from pathlib import Path

from ci_lint.yaml_io import LoadStatus, YamlValue, load_yaml_file
from ci_lint.proc import run_captured


@dataclass(frozen=True)
class ParsedYamlFile:
    path: str  # repo-relative, forward-slash
    document: YamlValue | None
    status: LoadStatus
    reason: str | None


def _rel(repo_root: Path, path: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def discover_workflow_files(repo_root: Path) -> list[Path]:
    wf_dir = repo_root / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    out = sorted(p for p in wf_dir.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml"))
    return out


def discover_composite_actions(repo_root: Path) -> list[Path]:
    out: list[Path] = []
    actions_dir = repo_root / ".github" / "actions"
    if actions_dir.is_dir():
        out.extend(sorted(actions_dir.rglob("action.yml")))
        out.extend(sorted(actions_dir.rglob("action.yaml")))
    root_action = repo_root / "action.yml"
    if root_action.is_file():
        out.append(root_action)
    root_action_yaml = repo_root / "action.yaml"
    if root_action_yaml.is_file():
        out.append(root_action_yaml)
    return out


def load(repo_root: Path, path: Path) -> ParsedYamlFile:
    result = load_yaml_file(path)
    return ParsedYamlFile(
        path=_rel(repo_root, path),
        document=result.document,
        status=result.status,
        reason=result.reason,
    )


def load_workflows(repo_root: Path) -> list[ParsedYamlFile]:
    return [load(repo_root, p) for p in discover_workflow_files(repo_root)]


def load_workflows_at(repo_root: Path, rev: str) -> tuple[ParsedYamlFile, ...]:
    """Read immutable base workflows through the existing YAML loader.

    Refuse unavailable or unparseable source; never substitute the checkout.
    """
    if re.fullmatch(r"[0-9a-f]{40}", rev) is None:
        raise ValueError("workflow policy requires an exact Git commit")
    listed = run_captured(["git", "ls-tree", "-r", "-z", "--name-only", rev, "--", ".github/workflows"], cwd=repo_root)
    if not listed.ok:
        raise ValueError("base workflows unavailable")
    files: list[ParsedYamlFile] = []
    with tempfile.TemporaryDirectory(prefix="ci-base-workflows-") as scratch:
        staged = Path(scratch) / "workflow.yml"
        for name in listed.stdout.split("\0"):
            path = Path(name)
            if path.parent.as_posix() != ".github/workflows" or path.suffix not in (".yml", ".yaml"):
                continue
            source = run_captured(["git", "show", f"{rev}:{name}"], cwd=repo_root)
            if not source.ok:
                raise ValueError("base workflow unavailable")
            staged.write_text(source.stdout, encoding="utf-8")
            loaded = load_yaml_file(staged)
            if loaded.status != LoadStatus.OK or not isinstance(loaded.document, dict):
                raise ValueError("base workflow policy cannot be parsed")
            files.append(ParsedYamlFile(name, loaded.document, loaded.status, loaded.reason))
    return tuple(files)


def load_composite_actions(repo_root: Path) -> list[ParsedYamlFile]:
    return [load(repo_root, p) for p in discover_composite_actions(repo_root)]


def as_dict(value: YamlValue) -> dict[str, YamlValue]:
    return value if isinstance(value, dict) else {}


def as_list(value: YamlValue) -> list[YamlValue]:
    return value if isinstance(value, list) else []


def get_on_section(doc: dict[str, YamlValue]) -> dict[str, YamlValue]:
    """The `on:` trigger section, robust to YAML 1.1's "Norway problem": a
    bare `on:` key parses as the boolean `True` under PyYAML's safe_load
    (and often ends up as the string "true" once round-tripped through
    `yq -o=json`). Try every form before giving up."""

    value: YamlValue = None
    found = False
    for key in ("on", True, "true", "True"):
        if key in doc:
            value = doc[key]
            found = True
            break
    if not found:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {str(v): None for v in value}
    if isinstance(value, str):
        return {value: None}
    return {}


def jobs_of(doc: dict[str, YamlValue]) -> dict[str, dict[str, YamlValue]]:
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return {}
    return {k: v for k, v in jobs.items() if isinstance(v, dict)}


def steps_of(job: dict[str, YamlValue]) -> list[dict[str, YamlValue]]:
    steps = job.get("steps")
    if not isinstance(steps, list):
        return []
    return [s for s in steps if isinstance(s, dict)]
