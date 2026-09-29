"""Group 2: workflow rules.

GEN-001, GEN-002, GEN-008, TAG-003, SEC-003, SEC-004, RUN-001, RUN-002,
WF-001, WF-002, WF-003, and CT-004 (the linter pin, verified against the actual
checkout step here since CT-001..003/005 live in schema/exceptions). All of
these need parsed workflow YAML; a file that fails to parse (no PyYAML, no
`yq` on PATH) makes every rule that depends on it `needs_review` for that
file rather than silently passing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ci_lint.finding import Finding, Status
from ci_lint.schema import CiToml
from ci_lint.workflow_scan import (
    ParsedYamlFile,
    as_dict,
    as_list,
    get_on_section,
    jobs_of,
    load_composite_actions,
    load_workflows,
    steps_of,
)
from ci_lint.yaml_io import LoadStatus, YamlValue

FLEET_RUNNERS: frozenset[str] = frozenset(
    {
        "ubuntu-24.04",
        "ubuntu-24.04-arm",
        "windows-2025",
        "windows-11-arm",
        "macos-15",
        "macos-15-intel",
    }
)
NON_LINUX_FLEET_LABELS: frozenset[str] = frozenset(
    {"windows-2025", "windows-11-arm", "macos-15", "macos-15-intel"}
)
SHA40_RE = re.compile(r"^[0-9a-fA-F]{40}$")

WORKFLOW_FILE_RULES: tuple[str, ...] = (
    "GEN-001",
    "GEN-002",
    "GEN-008",
    "TAG-003",
    "SEC-003",
    "SEC-004",
    "RUN-001",
    "RUN-002",
    "WF-001",
    "WF-002",
    "WF-003",
    "CT-004",
)
ACTION_FILE_RULES: tuple[str, ...] = ("SEC-004", "WF-003")


def _needs_review_for_unparsed(
    files: list[ParsedYamlFile], rules: tuple[str, ...]
) -> list[Finding]:
    findings: list[Finding] = []
    for f in files:
        if f.status != LoadStatus.NEEDS_REVIEW:
            continue
        for rule in rules:
            findings.append(
                Finding(
                    rule=rule,
                    path=f.path,
                    status=Status.NEEDS_REVIEW,
                    message=f"cannot evaluate {rule} for {f.path}: {f.reason}",
                    fix="make PyYAML importable or `yq` available on PATH so ci-lint can parse "
                    f"{f.path}, then re-run precheck",
                )
            )
    return findings


def _trigger_strings(doc: dict[str, YamlValue]) -> list[str]:
    on = get_on_section(doc)
    out: list[str] = []
    for key, val in on.items():
        if key == "push":
            branches: list[str] = []
            if isinstance(val, dict):
                raw = val.get("branches")
                if isinstance(raw, list):
                    branches = [b for b in raw if isinstance(b, str)]
            out.append("push:main" if branches == ["main"] else "push")
        else:
            out.append(key)
    return out


def check_gen_001(workflows: list[ParsedYamlFile]) -> list[Finding]:
    match = next((w for w in workflows if w.path == ".github/workflows/ci.yml"), None)
    if match is None:
        return [
            Finding(
                rule="GEN-001",
                message="no .github/workflows/ci.yml found",
                fix="add .github/workflows/ci.yml as the pull_request entry point "
                "(see [allow].workflows in ci.toml)",
            )
        ]
    if match.status != LoadStatus.OK:
        return []  # already reported as needs_review
    on = get_on_section(as_dict(match.document))
    if "pull_request" not in on:
        return [
            Finding(
                rule="GEN-001",
                path=match.path,
                message="ci.yml does not declare an 'on.pull_request' trigger",
                fix="add 'pull_request:' under ci.yml's 'on:' section",
            )
        ]
    return []


def check_gen_008(ci: CiToml, workflows: list[ParsedYamlFile]) -> list[Finding]:
    findings: list[Finding] = []
    pr_files: list[str] = []
    for wf in workflows:
        if wf.status != LoadStatus.OK:
            continue
        basename = Path(wf.path).name
        doc = as_dict(wf.document)
        triggers = _trigger_strings(doc)
        if "pull_request" in triggers:
            pr_files.append(wf.path)
        allowed = ci.allow.workflows.get(basename)
        if allowed is None:
            findings.append(
                Finding(
                    rule="GEN-008",
                    path=wf.path,
                    message=f"workflow file '{basename}' is not declared in ci.toml's [allow].workflows",
                    fix=f'add "{basename}" = [...] to ci.toml\'s [allow].workflows, listing its exact '
                    f"triggers, or delete {wf.path}",
                )
            )
            continue
        for trig in triggers:
            if trig not in allowed:
                findings.append(
                    Finding(
                        rule="GEN-008",
                        path=wf.path,
                        message=f"workflow '{basename}' declares trigger '{trig}', which is not in "
                        f"its [allow].workflows entry {list(allowed)}",
                        fix=f'add "{trig}" to allow.workflows."{basename}" in ci.toml, or remove that '
                        f"trigger from {wf.path}",
                    )
                )
    if len(pr_files) > 1:
        findings.append(
            Finding(
                rule="GEN-008",
                message=f"{len(pr_files)} workflow files declare pull_request: {pr_files}",
                fix="keep 'pull_request:' in exactly one workflow file (.github/workflows/ci.yml); "
                "remove it from the others",
            )
        )
    return findings


def check_tag_003(workflows: list[ParsedYamlFile]) -> list[Finding]:
    findings: list[Finding] = []
    for wf in workflows:
        if wf.status != LoadStatus.OK:
            continue
        on = get_on_section(as_dict(wf.document))
        if "pull_request" not in on:
            continue
        pr_val = on["pull_request"]
        types: list[YamlValue] | None = None
        if isinstance(pr_val, dict):
            raw = pr_val.get("types")
            if isinstance(raw, list):
                types = raw
        if types is None or "edited" not in types:
            findings.append(
                Finding(
                    rule="TAG-003",
                    path=wf.path,
                    message="'on.pull_request.types' does not include 'edited' "
                    "(a title-only tag edit would not re-trigger the run)",
                    fix="add 'types: [opened, synchronize, reopened, edited]' under on.pull_request "
                    f"in {wf.path}",
                )
            )
    return findings


def check_sec_003(workflows: list[ParsedYamlFile]) -> list[Finding]:
    findings: list[Finding] = []
    for wf in workflows:
        if wf.status != LoadStatus.OK:
            continue
        on = get_on_section(as_dict(wf.document))
        for bad in ("pull_request_target", "workflow_run"):
            if bad in on:
                findings.append(
                    Finding(
                        rule="SEC-003",
                        path=wf.path,
                        message=f"workflow declares '{bad}:', which runs with base-branch privileges "
                        "against untrusted PR content",
                        fix=f"remove '{bad}:' from {wf.path}; use 'pull_request:' with the default "
                        "read-only token instead",
                    )
                )
    return findings


def _iter_uses(
    doc: dict[str, YamlValue], *, is_composite: bool
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if is_composite:
        runs = doc.get("runs")
        if isinstance(runs, dict):
            for i, step in enumerate(as_list(runs.get("steps"))):
                if isinstance(step, dict) and isinstance(step.get("uses"), str):
                    out.append((step["uses"], f"runs.steps[{i}]"))
    else:
        for job_id, job in jobs_of(doc).items():
            for i, step in enumerate(steps_of(job)):
                uses = step.get("uses")
                if isinstance(uses, str):
                    out.append((uses, f"jobs.{job_id}.steps[{i}]"))
    return out


# zackees/ci.yml#33 (closes #18/#31's SHA-pinning-vs-float tension): the ONE
# sanctioned first-party floating ref SEC-004 accepts in place of a 40-hex
# SHA. `zackees/setup-soldr@v0`'s `v0` tag moves only through setup-soldr's
# own gated promotion (`update-v0-tag.yml`'s exact-main contract plus a
# FastLED/fbuild canary), not an arbitrary maintainer push, unlike a
# third-party action's floating tag -- so this does not generalize to any
# other action, first- or third-party. `/cook` and `/cleanup` are
# setup-soldr's own sub-actions and share its release cadence. A repository
# with GitHub's own `sha_pinning_required` setting on cannot use this float
# (that setting rejects `@v0` before any job runs) and must SHA-pin instead
# -- `RUST-014` (docs/policy-rust.md) then requires that pin stay fresh.
SETUP_SOLDR_FLOAT_REPO = "zackees/setup-soldr"
SETUP_SOLDR_FLOAT_REF = "v0"


def _is_sanctioned_setup_soldr_float(repo_slug: str, ref: str) -> bool:
    return repo_slug == SETUP_SOLDR_FLOAT_REPO and ref == SETUP_SOLDR_FLOAT_REF


def _check_one_use(uses: str, path: str, loc: str, allowed: frozenset[str]) -> list[Finding]:
    if uses.startswith("./") or uses.startswith("docker://"):
        return []
    if "@" not in uses:
        return [
            Finding(
                rule="SEC-004",
                path=path,
                message=f"{loc}: uses '{uses}' has no @ref",
                fix=f"pin {loc} to a 40-character commit SHA: 'uses: {uses}@<40-hex-sha>  # vX.Y.Z'",
            )
        ]
    slug, ref = uses.split("@", 1)
    repo_slug = "/".join(slug.split("/")[:2])
    out: list[Finding] = []
    if repo_slug not in allowed:
        out.append(
            Finding(
                rule="SEC-004",
                path=path,
                message=f"{loc}: action '{repo_slug}' is not in ci.toml's [allow].actions",
                fix=f'add "{repo_slug}" to ci.toml\'s [allow].actions, or replace {loc} with an '
                "allowlisted action",
            )
        )
    if not SHA40_RE.match(ref) and not _is_sanctioned_setup_soldr_float(repo_slug, ref):
        out.append(
            Finding(
                rule="SEC-004",
                path=path,
                message=f"{loc}: uses '{uses}' is not pinned to a 40-character commit SHA",
                fix=f"pin {loc} to the action's full commit SHA with a version comment: "
                f"'uses: {slug}@<40-hex-sha>  # vX.Y.Z' -- or, only for {SETUP_SOLDR_FLOAT_REPO}, "
                f"float at '@{SETUP_SOLDR_FLOAT_REF}' (zackees/ci.yml#33's one sanctioned "
                "first-party float; every other action stays SHA-pinned)",
            )
        )
    return out


def check_sec_004(
    ci: CiToml, workflows: list[ParsedYamlFile], actions: list[ParsedYamlFile]
) -> list[Finding]:
    findings: list[Finding] = []
    allowed = frozenset(ci.allow.actions)
    for wf in workflows:
        if wf.status != LoadStatus.OK:
            continue
        for uses, loc in _iter_uses(as_dict(wf.document), is_composite=False):
            findings.extend(_check_one_use(uses, wf.path, loc, allowed))
    for act in actions:
        if act.status != LoadStatus.OK:
            continue
        for uses, loc in _iter_uses(as_dict(act.document), is_composite=True):
            findings.extend(_check_one_use(uses, act.path, loc, allowed))
    return findings


# A precheck-plan output that enumerates platform matrix legs built from
# ci.toml's [platforms] table (see `ci_lint.plan.PlatformLane.to_json_dict`,
# which emits a `runs_on` field per leg straight from `Platform.runs_on`).
# A `strategy.matrix.<key>` sourced from one of these, combined with a
# `runs-on: ${{ matrix.<key>.runs_on }}` expression using that same key, is
# resolvable statically: it can only ever take the values ci.toml's
# [platforms] declare, so RUN-001 validates those instead of giving up.
PLATFORM_LANES_OUTPUTS: frozenset[str] = frozenset(
    {"platform_lanes_todo_json", "platform_lanes_json"}
)
_NEEDS_OUTPUTS_RE = re.compile(r"needs\.[A-Za-z0-9_-]+\.outputs\.[A-Za-z0-9_]+")


def _matrix_platform_lane_key(job: dict[str, YamlValue]) -> str | None:
    """Return the `strategy.matrix` key (e.g. `"lane"`) whose value is a
    `fromJSON(needs.<job>.outputs.<platform-lanes output>)` expression, or
    None if this job's matrix isn't built from a precheck plan platform-
    lanes output."""
    strategy = job.get("strategy")
    if not isinstance(strategy, dict):
        return None
    matrix = strategy.get("matrix")
    if not isinstance(matrix, dict):
        return None
    for key, val in matrix.items():
        if not isinstance(val, str):
            continue
        if not _NEEDS_OUTPUTS_RE.search(val):
            continue
        if any(output in val for output in PLATFORM_LANES_OUTPUTS):
            return key
    return None


def _check_platform_runs_on(ci: CiToml) -> list[Finding]:
    """RUN-001 resolved for the matrix case: validate ci.toml's own
    [platforms].*.runs-on values, since that is what the matrix leg's
    `runs_on` field can ever contain at runtime."""
    findings: list[Finding] = []
    for pid, platform in sorted(ci.platforms.items()):
        label = platform.runs_on
        if not label:
            continue
        if label in FLEET_RUNNERS:
            continue
        reason = (
            "a '-latest' label (retires without notice)"
            if label.endswith("-latest")
            else "not in the fleet runner list"
        )
        findings.append(
            Finding(
                rule="RUN-001",
                path="ci.toml",
                message=f"[platforms.{pid}].runs-on = '{label}' is {reason} (resolved from a "
                "matrix built over ci.toml's [platforms] and used as 'runs-on: "
                "${{ matrix.<var>.runs_on }}')",
                fix=f"set [platforms.{pid}].runs-on in ci.toml to one of the fleet labels: "
                f"{sorted(FLEET_RUNNERS)}",
            )
        )
    return findings


def check_run_001(ci: CiToml, workflows: list[ParsedYamlFile]) -> list[Finding]:
    findings: list[Finding] = []
    platform_runs_on_checked = False
    for wf in workflows:
        if wf.status != LoadStatus.OK:
            continue
        for job_id, job in jobs_of(as_dict(wf.document)).items():
            runs_on = job.get("runs-on")
            labels: list[str] = []
            if isinstance(runs_on, str):
                labels = [runs_on]
            elif isinstance(runs_on, list):
                labels = [x for x in runs_on if isinstance(x, str)]
            lane_key = _matrix_platform_lane_key(job)
            for label in labels:
                if "${{" in label:
                    if lane_key is not None and f"matrix.{lane_key}.runs_on" in label:
                        if not platform_runs_on_checked:
                            findings.extend(_check_platform_runs_on(ci))
                            platform_runs_on_checked = True
                        continue
                    findings.append(
                        Finding(
                            rule="RUN-001",
                            path=wf.path,
                            status=Status.NEEDS_REVIEW,
                            message=f"jobs.{job_id}.runs-on is a dynamic expression '{label}'; cannot "
                            "statically verify it against the fleet runner list",
                            fix="resolve what the expression evaluates to and confirm it is one of the "
                            "fleet labels documented in docs/ci-toml.md",
                        )
                    )
                elif label not in FLEET_RUNNERS:
                    reason = "a '-latest' label (retires without notice)" if label.endswith(
                        "-latest"
                    ) else "not in the fleet runner list"
                    findings.append(
                        Finding(
                            rule="RUN-001",
                            path=wf.path,
                            message=f"jobs.{job_id}.runs-on = '{label}' is {reason}",
                            fix="use one of the fleet labels: "
                            f"{sorted(FLEET_RUNNERS)}",
                        )
                    )
    return findings


_BUILD_HOST_ANNOTATION_RE = re.compile(r"\(on\s+[\w.\-]+", re.IGNORECASE)
_NATIVE_ANNOTATION_RE = re.compile(r"\(native\b", re.IGNORECASE)


def check_run_002(workflows: list[ParsedYamlFile]) -> list[Finding]:
    """RUN-002 (ci.yml#12): a cross-compiled job's display name must show
    where it builds and where it runs. Detection reuses RUN-001's
    platform-lanes matrix signal (`_matrix_platform_lane_key`) -- a job
    whose `strategy.matrix` is built from the precheck plan's
    `platform_lanes_json`/`platform_lanes_todo_json` output is one leg of a
    cross-compile pipeline:

    - a leg whose `runs-on` is a STATIC label (it always builds on that
      host, regardless of the lane's target) must say so in its `name:`,
      e.g. `... (on ubuntu-24.04, soldr) ...`, so a glance at the Checks
      tab shows the build never happens on the target platform.
    - a leg whose `runs-on` is the dynamic `${{ matrix.<key>.runs_on }}`
      expression (it executes natively on the lane's own runner, with
      compiling assumed impossible) must say so, e.g. `... (native) ...`.
    """
    findings: list[Finding] = []
    for wf in workflows:
        if wf.status != LoadStatus.OK:
            continue
        for job_id, job in jobs_of(as_dict(wf.document)).items():
            lane_key = _matrix_platform_lane_key(job)
            if lane_key is None:
                continue
            runs_on = job.get("runs-on")
            name = job.get("name")
            name_str = name if isinstance(name, str) else ""
            is_dynamic_lane_runner = (
                isinstance(runs_on, str) and f"matrix.{lane_key}.runs_on" in runs_on
            )
            if is_dynamic_lane_runner:
                if _NATIVE_ANNOTATION_RE.search(name_str):
                    continue
                findings.append(
                    Finding(
                        rule="RUN-002",
                        path=wf.path,
                        message=f"jobs.{job_id} executes natively on its lane's own runner "
                        f"(runs-on: {runs_on}) but its `name:` does not say so",
                        fix=f"add a '(native)' (or '(native {{runner}})') annotation to "
                        f"jobs.{job_id}.name so the Checks tab shows this job runs on the "
                        "target platform and never compiles there",
                    )
                )
            elif isinstance(runs_on, str) and "${{" not in runs_on:
                if _BUILD_HOST_ANNOTATION_RE.search(name_str):
                    continue
                findings.append(
                    Finding(
                        rule="RUN-002",
                        path=wf.path,
                        message=f"jobs.{job_id} is one leg of a cross-compile matrix but always "
                        f"builds on a fixed host (runs-on: {runs_on}); its `name:` does not "
                        "say where it builds",
                        fix=f"add a '(on {runs_on}, ...)' annotation to jobs.{job_id}.name "
                        "(e.g. '(on ubuntu-24.04, soldr)') so the Checks tab shows the build "
                        "host, not just the target",
                    )
                )
    return findings


def check_wf_001(workflows: list[ParsedYamlFile]) -> list[Finding]:
    findings: list[Finding] = []
    for wf in workflows:
        if wf.status != LoadStatus.OK:
            continue
        for job_id, job in jobs_of(as_dict(wf.document)).items():
            if "uses" in job:
                continue  # a reusable-workflow-call job has no timeout-minutes of its own
            if "timeout-minutes" not in job:
                findings.append(
                    Finding(
                        rule="WF-001",
                        path=wf.path,
                        message=f"job '{job_id}' has no timeout-minutes",
                        fix=f"add 'timeout-minutes: <n>' to jobs.{job_id} in {wf.path}",
                    )
                )
    return findings


def check_wf_002(workflows: list[ParsedYamlFile]) -> list[Finding]:
    findings: list[Finding] = []
    for wf in workflows:
        if wf.status != LoadStatus.OK:
            continue
        if Path(wf.path).name == "ci-pre.yml":
            # zackees/ci.yml#23 §3: ci-pre.yml's concurrency is per job (only
            # the cache-janitor job is serialized); a workflow-level group is
            # GEN-015 there (ci_lint.rules.ci_pre), not a WF-002 requirement.
            continue
        if "concurrency" not in as_dict(wf.document):
            findings.append(
                Finding(
                    rule="WF-002",
                    path=wf.path,
                    message="workflow has no top-level 'concurrency' key",
                    fix="add a top-level 'concurrency: { group: <expr>, cancel-in-progress: true }' to "
                    f"{wf.path}",
                )
            )
    return findings


def check_wf_003(
    workflows: list[ParsedYamlFile], actions: list[ParsedYamlFile]
) -> list[Finding]:
    findings: list[Finding] = []
    for wf in workflows:
        if wf.status != LoadStatus.OK:
            continue
        for job_id, job in jobs_of(as_dict(wf.document)).items():
            if "continue-on-error" in job:
                findings.append(
                    Finding(
                        rule="WF-003",
                        path=wf.path,
                        message=f"jobs.{job_id} sets continue-on-error",
                        fix=f"remove 'continue-on-error' from jobs.{job_id} in {wf.path}; fix or gate "
                        "the failing step instead",
                    )
                )
            for i, step in enumerate(steps_of(job)):
                if "continue-on-error" in step:
                    findings.append(
                        Finding(
                            rule="WF-003",
                            path=wf.path,
                            message=f"jobs.{job_id}.steps[{i}] sets continue-on-error",
                            fix=f"remove 'continue-on-error' from jobs.{job_id}.steps[{i}] in {wf.path}",
                        )
                    )
    for act in actions:
        if act.status != LoadStatus.OK:
            continue
        runs = as_dict(act.document).get("runs")
        if isinstance(runs, dict):
            for i, step in enumerate(as_list(runs.get("steps"))):
                if isinstance(step, dict) and "continue-on-error" in step:
                    findings.append(
                        Finding(
                            rule="WF-003",
                            path=act.path,
                            message=f"runs.steps[{i}] sets continue-on-error",
                            fix=f"remove 'continue-on-error' from runs.steps[{i}] in {act.path}",
                        )
                    )
    return findings


def _looks_non_linux(label: str) -> bool | None:
    if "${{" in label:
        return None
    if label in NON_LINUX_FLEET_LABELS:
        return True
    lower = label.lower()
    if "windows" in lower or "macos" in lower or "win-" in lower:
        return True
    if label.startswith("ubuntu") or "linux" in lower:
        return False
    return None


def check_gen_002(workflows: list[ParsedYamlFile]) -> list[Finding]:
    findings: list[Finding] = []
    for wf in workflows:
        if wf.status != LoadStatus.OK:
            continue
        for job_id, job in jobs_of(as_dict(wf.document)).items():
            runs_on = job.get("runs-on")
            labels: list[str] = []
            if isinstance(runs_on, str):
                labels = [runs_on]
            elif isinstance(runs_on, list):
                labels = [x for x in runs_on if isinstance(x, str)]
            if not any(_looks_non_linux(label) for label in labels):
                continue
            if_expr = job.get("if")
            haystacks: list[str] = []
            if isinstance(if_expr, str):
                haystacks.append(if_expr)
            strategy = job.get("strategy")
            if isinstance(strategy, dict) and "matrix" in strategy:
                haystacks.append(json.dumps(strategy["matrix"], default=str))
            if any("needs.precheck.outputs" in h for h in haystacks):
                continue
            findings.append(
                Finding(
                    rule="GEN-002",
                    path=wf.path,
                    message=f"job '{job_id}' runs on non-Linux runner(s) {labels} without an "
                    "if:/matrix derived from needs.precheck.outputs",
                    fix=f"gate jobs.{job_id} with an 'if:' or 'strategy.matrix' expression that "
                    "references needs.precheck.outputs, so only the platforms the precheck plan "
                    "selected actually run",
                )
            )
    return findings


def check_ct_004(ci: CiToml, workflows: list[ParsedYamlFile]) -> list[Finding]:
    if ci.linter_sha is None:
        return []
    findings: list[Finding] = []
    found_checkout = False
    for wf in workflows:
        if wf.status != LoadStatus.OK:
            continue
        for job_id, job in jobs_of(as_dict(wf.document)).items():
            for i, step in enumerate(steps_of(job)):
                uses = step.get("uses")
                if not isinstance(uses, str) or not uses.startswith("actions/checkout@"):
                    continue
                with_ = step.get("with")
                if not isinstance(with_, dict) or with_.get("repository") != "zackees/ci.yml":
                    continue
                found_checkout = True
                ref = with_.get("ref")
                if ref != ci.linter_sha:
                    findings.append(
                        Finding(
                            rule="CT-004",
                            path=wf.path,
                            message=f"jobs.{job_id}.steps[{i}] checks out zackees/ci.yml at "
                            f"ref={ref!r}, which does not match ci.toml's linter pin "
                            f"'{ci.linter_sha}'",
                            fix=f"set with.ref to '{ci.linter_sha}' in {wf.path} (jobs.{job_id}."
                            f"steps[{i}]), or update ci.toml's 'linter' field and re-run precheck",
                        )
                    )
    if not found_checkout:
        findings.append(
            Finding(
                rule="CT-004",
                message="no workflow step checks out zackees/ci.yml (needed to run ci-lint's precheck)",
                fix="add an actions/checkout step with 'repository: zackees/ci.yml' and "
                f"'ref: {ci.linter_sha}' to the workflow that runs precheck",
            )
        )
    return findings


def check_group2(ci: CiToml, repo_root: Path) -> list[Finding]:
    workflows = load_workflows(repo_root)
    actions = load_composite_actions(repo_root)

    findings: list[Finding] = []
    findings.extend(_needs_review_for_unparsed(workflows, WORKFLOW_FILE_RULES))
    findings.extend(_needs_review_for_unparsed(actions, ACTION_FILE_RULES))

    findings.extend(check_gen_001(workflows))
    findings.extend(check_gen_008(ci, workflows))
    findings.extend(check_tag_003(workflows))
    findings.extend(check_sec_003(workflows))
    findings.extend(check_sec_004(ci, workflows, actions))
    findings.extend(check_run_001(ci, workflows))
    findings.extend(check_run_002(workflows))
    findings.extend(check_wf_001(workflows))
    findings.extend(check_wf_002(workflows))
    findings.extend(check_wf_003(workflows, actions))
    findings.extend(check_gen_002(workflows))
    findings.extend(check_ct_004(ci, workflows))
    return findings
