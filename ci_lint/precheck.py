"""The static precheck: run every group 1-10 rule, cheapest first, and
report every finding (round-1A brief, section B).

Exit code: 1 if any finding's status is `violation` after exceptions are
applied; 0 otherwise. A `needs_review` finding does not by itself fail the
run (agent-guide.md: it is a distinct status from both pass and violation),
but it is always printed prominently so it cannot be silently treated as a
pass.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from ci_lint.exceptions import apply_exceptions
from ci_lint.finding import Finding, Status
from ci_lint.rules.cache_static import check_group9
from ci_lint.rules.contract import check_ct_006, check_tag_001, check_tag_002
from ci_lint.rules.layout import check_group6
from ci_lint.rules.packaging import check_group8
from ci_lint.rules.rust_units import check_group7
from ci_lint.rules.secrets_rules import check_group5
from ci_lint.rules.shell import check_group3
from ci_lint.rules.tools import check_group4
from ci_lint.rules.workflows import check_group2
from ci_lint.schema import CiToml, load_ci_toml

GROUP_LABELS: dict[int, str] = {
    1: "contract (CT-001..006, TAG-001, TAG-002)",
    2: "workflows (GEN-001/002/008, TAG-003, SEC-003/004, RUN-001, WF-001..003, CT-004)",
    3: "shell budget (GEN-005)",
    4: "tools (TOOL-001/002, CACHE-009)",
    5: "secrets (SEC-001/002)",
    6: "layout (LAYOUT-001)",
    7: "tests & units (RUST-005/011/012)",
    8: "packaging (PKG-003/004/005)",
    9: "cache static (CACHE-001/002/004)",
    10: "lint pin (CT-004, checked inside group 2)",
}


@dataclass(frozen=True)
class PrecheckResult:
    findings: tuple[Finding, ...]
    elapsed_seconds: float
    ci: CiToml | None
    cache_arithmetic: str | None


def run_precheck(repo_root: Path, *, title: str = "") -> PrecheckResult:
    start = time.monotonic()
    ci, findings = load_ci_toml(repo_root)
    all_findings: list[Finding] = list(findings)
    cache_arithmetic: str | None = None

    if ci is not None:
        all_findings.extend(check_tag_001(ci, title))
        all_findings.extend(check_tag_002(title))
        all_findings.extend(check_ct_006(ci, repo_root))

        all_findings.extend(check_group2(ci, repo_root))
        all_findings.extend(check_group3(ci, repo_root))
        all_findings.extend(check_group4(ci, repo_root))
        all_findings.extend(check_group5(repo_root))
        all_findings.extend(check_group6(ci, repo_root))
        all_findings.extend(check_group7(ci, repo_root))
        all_findings.extend(check_group8(ci, repo_root))
        findings9, cache_arithmetic = check_group9(ci, repo_root)
        all_findings.extend(findings9)

        outcome = apply_exceptions(ci, all_findings)
        all_findings = outcome.findings

    elapsed = time.monotonic() - start
    return PrecheckResult(
        findings=tuple(all_findings), elapsed_seconds=elapsed, ci=ci, cache_arithmetic=cache_arithmetic
    )


def render_text(result: PrecheckResult) -> str:
    lines: list[str] = []
    by_rule: dict[str, list[Finding]] = {}
    for f in result.findings:
        by_rule.setdefault(f.rule, []).append(f)

    if not result.findings:
        lines.append("ci-lint precheck: no findings.")
    for rule in sorted(by_rule):
        lines.append(f"== {rule} ({len(by_rule[rule])}) ==")
        for f in by_rule[rule]:
            lines.append(f.render())
        lines.append("")

    if result.cache_arithmetic:
        lines.append(result.cache_arithmetic)
        lines.append("")

    n_violation = sum(1 for f in result.findings if f.status == Status.VIOLATION)
    n_exception = sum(1 for f in result.findings if f.status == Status.APPROVED_EXCEPTION)
    n_review = sum(1 for f in result.findings if f.status == Status.NEEDS_REVIEW)
    lines.append(
        f"ci-lint precheck: {n_violation} violation(s), {n_exception} approved exception(s), "
        f"{n_review} needs_review, in {result.elapsed_seconds:.2f}s"
    )
    return "\n".join(lines)


def render_markdown_summary(result: PrecheckResult) -> str:
    lines: list[str] = ["# ci-lint precheck", ""]
    by_rule: dict[str, list[Finding]] = {}
    for f in result.findings:
        by_rule.setdefault(f.rule, []).append(f)
    if not result.findings:
        lines.append("No findings.")
    for rule in sorted(by_rule):
        lines.append(f"## {rule}")
        lines.append("")
        lines.append("| status | location | what | fix |")
        lines.append("| --- | --- | --- | --- |")
        for f in by_rule[rule]:
            lines.append(
                f"| {f.status.value} | `{f.location()}` | {f.message} | {f.fix} |"
            )
        lines.append("")
    if result.cache_arithmetic:
        lines.append("```")
        lines.append(result.cache_arithmetic)
        lines.append("```")
        lines.append("")
    n_violation = sum(1 for f in result.findings if f.status == Status.VIOLATION)
    lines.append(f"**{n_violation} violation(s)** in {result.elapsed_seconds:.2f}s")
    return "\n".join(lines)


def render_json(result: PrecheckResult) -> str:
    payload = [
        {
            "rule": f.rule,
            "status": f.status.value,
            "path": f.path,
            "line": f.line,
            "message": f.message,
            "fix": f.fix,
        }
        for f in result.findings
    ]
    return json.dumps(
        {
            "findings": payload,
            "elapsed_seconds": result.elapsed_seconds,
            "violations": sum(1 for f in result.findings if f.status == Status.VIOLATION),
        },
        indent=2,
    )


def has_violations(result: PrecheckResult) -> bool:
    return any(f.status == Status.VIOLATION for f in result.findings)


def write_step_summary(result: PrecheckResult) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(render_markdown_summary(result))
            fh.write("\n")
    except OSError:
        pass
