"""YAML loading with the fallback order the round brief fixes:

    PyYAML if importable -> `yq -o=json` if on PATH -> needs_review.

ci_lint stays standard-library-only: PyYAML and yq are optional accelerants,
never a hard dependency. Callers that cannot get a parsed document must treat
the subject as `needs_review`, never as passing (agent-guide.md: "A parser
that cannot resolve a GitHub expression or script must return needs review,
not pass.").
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ci_lint.proc import run_captured

# A YAML/JSON document, typed concretely (never Any) per AGENTS.md's
# boundary-dictionary rule. This is the JSON/YAML wire boundary itself.
YamlValue = None | bool | int | float | str | list["YamlValue"] | dict[str, "YamlValue"]


class LoadStatus(str, Enum):
    OK = "ok"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class LoadResult:
    status: LoadStatus
    document: YamlValue | None
    reason: str | None = None


def _try_pyyaml(text: str) -> LoadResult | None:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return None
    try:
        doc = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 - any parse failure is needs_review
        return LoadResult(LoadStatus.NEEDS_REVIEW, None, f"PyYAML parse error: {exc}")
    return LoadResult(LoadStatus.OK, doc, None)


def _try_yq(path: Path) -> LoadResult | None:
    yq = shutil.which("yq")
    if yq is None:
        return None
    try:
        proc = run_captured([yq, "-o=json", str(path)], timeout=10)
    except OSError as exc:
        return LoadResult(LoadStatus.NEEDS_REVIEW, None, f"yq invocation failed: {exc}")
    if proc.returncode != 0:
        return LoadResult(LoadStatus.NEEDS_REVIEW, None, f"yq exited {proc.returncode}: {proc.stderr.strip()}")
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return LoadResult(LoadStatus.NEEDS_REVIEW, None, f"yq produced invalid JSON: {exc}")
    return LoadResult(LoadStatus.OK, doc, None)


def load_yaml_file(path: Path) -> LoadResult:
    """Load one YAML file, trying PyYAML, then `yq -o=json`, then giving up."""

    if not path.is_file():
        return LoadResult(LoadStatus.NEEDS_REVIEW, None, f"{path} does not exist")
    text = path.read_text(encoding="utf-8")

    result = _try_pyyaml(text)
    if result is not None:
        return result

    result = _try_yq(path)
    if result is not None:
        return result

    return LoadResult(
        LoadStatus.NEEDS_REVIEW,
        None,
        "no YAML parser available (PyYAML not importable, yq not on PATH)",
    )


def yaml_tooling_available() -> bool:
    """True if this process can actually parse YAML (PyYAML or yq)."""

    try:
        import yaml  # noqa: F401  # type: ignore[import-untyped]

        return True
    except ImportError:
        pass
    return shutil.which("yq") is not None
