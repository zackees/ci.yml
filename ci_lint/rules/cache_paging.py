"""CACHE-033: reading the Actions cache must paginate.

`GET /repos/{repo}/actions/caches` returns at most `per_page` entries. A read
that stops after one page does not fail -- it returns a *shorter, plausible*
list, and every caller that sums bytes undercounts silently.

Measured 2026-10-05 across the fleet, which is how this rule was found:

    zackees/wezterm   22713 entries
    zackees/zccache      246
    zackees/bosn         214
    FastLED/FastLED      157
    zackees/soldr        152
    FastLED/fbuild       129
    zackees/ai-tools     114
    zackees/mimalloc-pprof 110

A single-page read of `zackees/zccache` reported 7.57 GiB instead of 7.67;
`FastLED/fbuild` reported 5.81 GiB (58% of the cap) instead of 10.06 GiB
(101%) -- a repository read as "worth declaring now" when it was already
over. Undercounting a cache is the one direction that makes a footprint
check pass that should have failed.

This rule flags a script or `run:` block that reads `actions/caches` without
walking pages, in the same surfaces GHAPI-001 already scans (workflows,
composite actions, `ci/` and tool scripts).

`ci_lint`'s own listing (`ci_lint.cache.github_cache.list_caches`) walks
pages and RAISES at its ceiling rather than returning a short list, so this
is the rule for repository-side code, not for ci-lint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ci_lint.finding import Finding
from ci_lint.repo_files import list_repo_files
from ci_lint.workflow_scan import as_dict, as_list, load_composite_actions, load_workflows
from ci_lint.yaml_io import LoadStatus

RULE = "CACHE-033"

_CACHES_URL = re.compile(r"/actions/caches")
# A page WALK looks like `page=1` / `page={page}` / a `rel="next"` Link
# read / gh's `--paginate`.
#
# `per_page` on its own is NOT evidence of one, and must not be accepted:
# `?per_page=100` with no `page=` is precisely the truncated read this rule
# exists to catch, and it is the most common way to write one.
# `page` must not be the tail of `per_page`, or every `?per_page=100`
# -- the exact truncated read -- would count as a walk.
_PAGINATED = re.compile(r"(?<![_A-Za-z])page\s*[={]|--paginate|rel\s*=\s*[\"']next[\"']")
# ci-lint's own paginated listing is the sanctioned reader.
_VIA_CI_LINT = re.compile(r"ci_lint[./]|from ci_lint|ci-lint cache")


@dataclass(frozen=True)
class CacheRead:
    path: str
    where: str
    line: int


@dataclass(frozen=True)
class RunBlock:
    """One `run:` block and where it sits, as a record rather than a
    positional tuple (PY-002)."""

    where: str
    run: str


def _unpaginated(text: str, path: str, where: str) -> list[CacheRead]:
    """Lines that touch `/actions/caches` on a page-unaware read."""

    if not _CACHES_URL.search(text):
        return []
    if _PAGINATED.search(text) or _VIA_CI_LINT.search(text):
        return []
    # One finding per file or `run:` block, anchored on the first line that
    # actually reads the endpoint -- not one per line of the block.
    for i, line in enumerate(text.splitlines(), 1):
        if _CACHES_URL.search(line):
            return [CacheRead(path=path, where=where, line=i)]
    return []


def _shell_findings(text: str, path: str, where: str) -> list[Finding]:
    return [
        Finding(
            rule=RULE,
            path=path,
            line=hit.line,
            message=(
                "reads the Actions cache without pagination; GET /repos/{repo}/actions/caches "
                "returns at most one page, so this undercounts the cache footprint silently"
            ),
            fix=(
                "walk pages (`per_page=100&page=N` until a short page) or use "
                "`ci-lint cache audit --repo .`, which paginates and fails closed at its ceiling"
            ),
        )
        for hit in _unpaginated(text, path, where)
    ]


def _python_script_findings(repo_root: Path) -> list[Finding]:
    """`ci/` and tool scripts, repo-relative like GHAPI-001's scanner."""

    findings: list[Finding] = []
    for rel in list_repo_files(repo_root):
        if Path(rel).suffix != ".py":
            continue
        try:
            text = (repo_root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        findings.extend(_shell_findings(text, rel, "script"))
    return findings


def _run_blocks(document: object) -> tuple[RunBlock, ...]:
    """Every `run:` block. Workflows nest them under `jobs.<id>.steps`; a
    composite action under `runs.steps`."""

    out: list[RunBlock] = []
    for job_id, job in as_dict(as_dict(document).get("jobs")).items():
        for i, step in enumerate(as_list(as_dict(job).get("steps"))):
            run = as_dict(step).get("run")
            if isinstance(run, str):
                out.append(RunBlock(where=f"jobs.{job_id}.steps[{i}]", run=run))
    steps = as_dict(as_dict(document).get("runs")).get("steps")
    for i, step in enumerate(as_dict(s) for s in as_list(steps)):
        run = as_dict(step).get("run")
        if isinstance(run, str):
            out.append(RunBlock(where=f"runs.steps[{i}]", run=run))
    return tuple(out)


def check_cache_033(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    docs = [
        (wf.document, wf.path)
        for wf in load_workflows(repo_root)
        if wf.status == LoadStatus.OK
    ]
    docs += [
        (act.document, act.path)
        for act in load_composite_actions(repo_root)
        if act.status == LoadStatus.OK
    ]
    for document, path in docs:
        for block in _run_blocks(document):
            findings.extend(_shell_findings(block.run, path, block.where))
    findings.extend(_python_script_findings(repo_root))
    return findings
