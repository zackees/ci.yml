"""GATE-012: a check that cannot run under bosn -> act never gates, delays,
or second-guesses an ordinary pull request (zackees/ci.yml#206).

The fleet runs local gate first (GATE-001..010): every check that can block
a PR must be one a developer reproduces locally, under bosn -> act. act runs
workflow jobs in Linux containers with no GitHub backend, so it cannot run a
GitHub App (CodeRabbit, Codecov, SonarCloud, ...), a GitHub-side service
(code-scanning upload, dependency review, Pages deploys), or anything that
needs an OIDC token. Such a check is suppressed, or confined to events that
never gate a PR, and nothing ever waits on it.

Static (`ci-lint remote-only`, and folded into `precheck`/`local-gate lint`):

- `.coderabbit.yaml` (or `.yml`) is missing, or leaves automatic review,
  the commit status, the fail-on-error status, or the auto-approve workflow
  on: a violation. Unparseable is `needs_review`.
- A job of a `pull_request`/`pull_request_target`/`merge_group` workflow (or
  of a local reusable workflow such a job calls) uses an act-impossible
  action or requests `id-token: write`: a violation when unconditional, a
  pass when its job- or step-level `if:` confines it to non-PR events or an
  opt-in label, `needs_review` when the `if:` is not recognized.
- A workflow/composite-action step or a `ci/` script waits on or polls for a
  GitHub App check (a wait-on-check action naming one, or a `run:`/script
  that polls statuses or check runs and names one): a violation. Naming one
  without a polling signal is `needs_review`. A same-line
  `# ci-lint: allow GATE-012 <reason>` excuses one line.

Live (`ci-lint audit`, reusing the branch-protection and rulesets payloads
GEN-006/GEN-011 already fetch): a required status check naming a GitHub App
check, or bound to an app other than GitHub Actions, is a violation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ci_lint.cargo_messages import JsonValue
from ci_lint.finding import Finding, Status
from ci_lint.rules.test_invocations import allowed, raw_lines_of, with_yaml_comment
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
from ci_lint.yaml_io import LoadStatus, YamlValue, load_yaml_file

RULE = "GATE-012"
POLICY = "docs/policy-general.md#remote-only-checks-never-gate-a-pr-gate-012"
PR_EVENTS: frozenset[str] = frozenset({"pull_request", "pull_request_target", "merge_group"})
CODERABBIT_FILES: tuple[str, ...] = (".coderabbit.yaml", ".coderabbit.yml")
GITHUB_ACTIONS_APP_ID = 15368
CODERABBIT_SNIPPET = (
    "reviews: {auto_review: {enabled: false}, commit_status: false, fail_commit_status: false, "
    "request_changes_workflow: false}"
)


@dataclass(frozen=True)
class RemoteOnlyAction:
    """A `uses:` prefix act cannot run, and why."""

    prefix: str
    why: str


# The registry of act-impossible actions. Extend it together with the table
# in docs/policy-general.md's GATE-012 section.
REMOTE_ONLY_ACTIONS: tuple[RemoteOnlyAction, ...] = (
    RemoteOnlyAction("github/codeql-action/", "code scanning needs GitHub's code-scanning backend"),
    RemoteOnlyAction("actions/dependency-review-action", "needs GitHub's dependency graph API"),
    RemoteOnlyAction("actions/deploy-pages", "deploys to GitHub Pages"),
    RemoteOnlyAction("actions/configure-pages", "needs a GitHub Pages site"),
    RemoteOnlyAction("actions/attest-build-provenance", "needs an OIDC token and sigstore"),
    RemoteOnlyAction("actions/attest-sbom", "needs an OIDC token and sigstore"),
    RemoteOnlyAction("actions/attest", "needs an OIDC token and sigstore"),
    RemoteOnlyAction("pypa/gh-action-pypi-publish", "trusted publishing needs an OIDC token"),
    RemoteOnlyAction("dependabot/fetch-metadata", "needs a Dependabot PR"),
    RemoteOnlyAction("codecov/codecov-action", "uploads to the hosted Codecov service"),
    RemoteOnlyAction("coverallsapp/github-action", "uploads to the hosted Coveralls service"),
    RemoteOnlyAction("sonarsource/sonarcloud-github-action", "runs on the hosted SonarCloud service"),
    RemoteOnlyAction("sonarsource/sonarqube-scan-action", "runs on a hosted SonarQube server"),
    RemoteOnlyAction("coderabbitai/", "a CodeRabbit (GitHub App) action"),
)

# Lower-case substrings of check names GitHub Apps report. None is produced
# by a workflow job, so none can run under act.
APP_CHECK_NAMES: tuple[str, ...] = (
    "coderabbit",
    "codecov",
    "sonarcloud",
    "deepsource",
    "snyk",
    "gitguardian",
    "dependabot",
    "codeql",
    "code scanning",
)

WAIT_ACTIONS: tuple[str, ...] = (
    "lewagon/wait-on-check-action",
    "fountainhead/action-wait-for-check",
    "poseidon/wait-for-status-checks",
    "wechuli/allcheckspassed",
)

_POLLING = re.compile(
    r"gh\s+pr\s+checks|--watch|/statuses\b|/status\b|check-runs|check_runs|check-suites|check_suites"
    r"|statusCheckRollup|status_check_rollup|\bsleep\b|wait_for|wait-for|poll",
    re.IGNORECASE,
)
_SCRIPT_SUFFIXES: frozenset[str] = frozenset({".py", ".sh", ".bash", ".ps1", ".js", ".mjs", ".ts"})

# An `if:` that keeps a job or step off ordinary PRs. `&&` only narrows, so
# one match is enough; an `||` needs every disjunct to match.
_CONFINED = (
    re.compile(r"github\.event_name\s*!=\s*'(pull_request|pull_request_target|merge_group)'"),
    re.compile(r"github\.event_name\s*==\s*'(push|schedule|workflow_dispatch|release|repository_dispatch)'"),
    re.compile(r"startswith\(\s*github\.ref\s*,\s*'refs/(tags|heads)/"),
    re.compile(r"github\.ref\s*==\s*'refs/(heads|tags)/"),
    re.compile(r"github\.ref_type\s*==\s*'tag'"),
    re.compile(r"github\.ref_name\s*==\s*'"),
    re.compile(r"contains\(\s*github\.event\.pull_request\.labels"),
    re.compile(r"github\.event\.label\.name\s*=="),
    re.compile(r"^false$"),
)


class Confinement(str, Enum):
    NONE = "none"  # no `if:` -- runs on every PR
    CONFINED = "confined"
    UNKNOWN = "unknown"


def classify_if(expr: YamlValue) -> Confinement:
    if expr is None:
        return Confinement.NONE
    if expr is False:
        return Confinement.CONFINED
    if not isinstance(expr, str):
        return Confinement.UNKNOWN
    text = expr.strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2]
    text = re.sub(r"\s+", " ", text.replace('"', "'")).strip().lower()
    parts = [p.strip(" ()") for p in text.split("||")] if "||" in text else [text]
    if all(any(rx.search(part) for rx in _CONFINED) for part in parts):
        return Confinement.CONFINED
    return Confinement.UNKNOWN


# Bot-identity filters (`dependabot[bot]` in an exempt-authors list) are
# common and are not waits, so the wait scan leaves Dependabot out.
WAIT_CHECK_NAMES: tuple[str, ...] = tuple(n for n in APP_CHECK_NAMES if n != "dependabot")


def _app_check(text: str, names: tuple[str, ...] = APP_CHECK_NAMES) -> str | None:
    low = text.lower()
    return next((name for name in names if name in low), None)


def _remote_only(uses: str) -> RemoteOnlyAction | None:
    low = uses.lower()
    return next((a for a in REMOTE_ONLY_ACTIONS if low.startswith(a.prefix)), None)


# ── .coderabbit.yaml ───────────────────────────────────────────────────────


def check_coderabbit(repo_root: Path) -> list[Finding]:
    path = next((repo_root / name for name in CODERABBIT_FILES if (repo_root / name).is_file()), None)
    fix = f"check in a repo-root .coderabbit.yaml with {CODERABBIT_SNIPPET} ({POLICY})"
    if path is None:
        return [
            Finding(
                rule=RULE,
                path=".coderabbit.yaml",
                message="no .coderabbit.yaml: CodeRabbit (a GitHub App act cannot run) reviews every PR "
                "on org defaults and posts a 'CodeRabbit' status",
                fix=fix,
            )
        ]
    rel = path.name
    loaded = load_yaml_file(path)
    if loaded.status != LoadStatus.OK:
        return [Finding(rule=RULE, path=rel, status=Status.NEEDS_REVIEW,
                        message=f"cannot parse {rel}: {loaded.reason}", fix=fix)]
    reviews = as_dict(as_dict(loaded.document).get("reviews"))
    problems: list[str] = []
    if as_dict(reviews.get("auto_review")).get("enabled") is not False:
        problems.append("reviews.auto_review.enabled is not false")
    if reviews.get("commit_status") is not False:
        problems.append("reviews.commit_status is not false")
    for key in ("request_changes_workflow", "fail_commit_status"):
        if reviews.get(key) is True:
            problems.append(f"reviews.{key} is true")
    return [Finding(rule=RULE, path=rel, message=f"CodeRabbit is not suppressed: {p}", fix=fix) for p in problems]


# ── workflows: act-impossible steps on PR events ──────────────────────────


def _id_token_write(permissions: YamlValue) -> bool:
    if permissions == "write-all":
        return True
    return as_dict(permissions).get("id-token") == "write"


def _local_reusable(uses: YamlValue) -> str | None:
    if isinstance(uses, str) and uses.startswith("./.github/workflows/"):
        return uses[2:].split("@", 1)[0]
    return None


def _confinement_finding(path: str, where: str, what: str, gate: Confinement) -> Finding | None:
    if gate == Confinement.CONFINED:
        return None
    if gate == Confinement.UNKNOWN:
        return Finding(
            rule=RULE, path=path, status=Status.NEEDS_REVIEW,
            message=f"{where}: {what}; its `if:` is not recognized as keeping it off ordinary PRs",
            fix="confine it with `if: github.event_name != 'pull_request'` (or a push/tag/schedule/"
            f"dispatch condition, or an opt-in label) so it never gates a PR ({POLICY})",
        )
    return Finding(
        rule=RULE, path=path,
        message=f"{where}: {what} on every PR, but act cannot run it, so no local gate can reproduce it",
        fix="move it to a push/tag/release/schedule/workflow_dispatch workflow, or add "
        f"`if: github.event_name != 'pull_request'` (or an opt-in label condition) ({POLICY})",
    )


def _combine(job_gate: Confinement, step_gate: Confinement) -> Confinement:
    if Confinement.CONFINED in (job_gate, step_gate):
        return Confinement.CONFINED
    if Confinement.UNKNOWN in (job_gate, step_gate):
        return Confinement.UNKNOWN
    return Confinement.NONE


def _job_findings(wf: ParsedYamlFile, document: YamlValue, context: Confinement) -> list[Finding]:
    """`context` is the confinement inherited from a caller (NONE for a
    directly PR-triggered workflow)."""

    doc = as_dict(document)
    findings: list[Finding] = []
    wf_oidc = _id_token_write(doc.get("permissions"))
    for job_id, job in jobs_of(doc).items():
        job_gate = _combine(context, classify_if(job.get("if")))
        where = f"job '{job_id}'"
        oidc = _id_token_write(job.get("permissions")) if "permissions" in job else wf_oidc
        if oidc:
            finding = _confinement_finding(wf.path, where, "requests `id-token: write` (act has no OIDC issuer)", job_gate)
            if finding is not None:
                findings.append(finding)
        for step in steps_of(job):
            uses = step.get("uses")
            action = _remote_only(uses) if isinstance(uses, str) else None
            if action is None:
                continue
            gate = _combine(job_gate, classify_if(step.get("if")))
            finding = _confinement_finding(wf.path, where, f"uses `{uses}` ({action.why})", gate)
            if finding is not None:
                findings.append(finding)
    return findings


def _pr_triggered(document: YamlValue) -> bool:
    return any(str(event) in PR_EVENTS for event in get_on_section(as_dict(document)))


def check_pr_workflows(repo_root: Path) -> list[Finding]:
    workflows = [wf for wf in load_workflows(repo_root) if wf.status == LoadStatus.OK]
    by_path = {wf.path: wf for wf in workflows}
    findings: list[Finding] = []
    seen: set[str] = set()
    for wf in workflows:
        if not _pr_triggered(wf.document):
            continue
        findings.extend(_job_findings(wf, wf.document, Confinement.NONE))
        for job in jobs_of(as_dict(wf.document)).values():
            callee = _local_reusable(job.get("uses"))
            gate = classify_if(job.get("if"))
            if callee is None or callee in seen or gate == Confinement.CONFINED:
                continue
            target = by_path.get(callee)
            if target is not None and not _pr_triggered(target.document):
                seen.add(callee)
                findings.extend(_job_findings(target, target.document, gate))
    return findings


# ── waiting on an app check ────────────────────────────────────────────────


def _text_findings(text: str, path: str, where: str, raw_lines: list[str]) -> list[Finding]:
    polls = _POLLING.search(text) is not None
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        name = _app_check(line, WAIT_CHECK_NAMES)
        if name is None or allowed(with_yaml_comment(line, raw_lines), RULE):
            continue
        status = Status.VIOLATION if polls else Status.NEEDS_REVIEW
        verb = "waits on / polls for" if polls else "names"
        findings.append(
            Finding(
                rule=RULE, path=path, status=status, line=lineno if where == "script" else None,
                message=f"{where} {verb} the GitHub App check '{name}' (`{line.strip()[:100]}`)",
                fix="never wait on or require a check act cannot run: an absent or suppressed app status "
                f"means zero wait ({POLICY}); a deliberate exception takes `# ci-lint: allow {RULE} <reason>`",
            )
        )
    return findings


def _step_wait_findings(steps: YamlValue, path: str, where: str, raw: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for step in (as_dict(s) for s in as_list(steps)):
        uses = step.get("uses")
        if isinstance(uses, str) and uses.lower().startswith(WAIT_ACTIONS):
            for key, value in as_dict(step.get("with")).items():
                name = _app_check(value, WAIT_CHECK_NAMES) if isinstance(value, str) else None
                if name is not None:
                    findings.append(
                        Finding(
                            rule=RULE, path=path,
                            message=f"{where}: `{uses}` waits on the GitHub App check '{name}' ({key}: {value})",
                            fix=f"drop the wait: a check act cannot run never gates or delays a PR ({POLICY})",
                        )
                    )
        run = step.get("run")
        if isinstance(run, str):
            findings.extend(_text_findings(run, path, where, raw))
    return findings


def check_waits(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for wf in load_workflows(repo_root):
        if wf.status != LoadStatus.OK:
            continue
        raw = raw_lines_of(repo_root / wf.path)
        for job_id, job in jobs_of(as_dict(wf.document)).items():
            findings.extend(_step_wait_findings(job.get("steps"), wf.path, f"job '{job_id}'", raw))
    for action in load_composite_actions(repo_root):
        if action.status != LoadStatus.OK:
            continue
        steps = as_dict(as_dict(action.document).get("runs")).get("steps")
        findings.extend(_step_wait_findings(steps, action.path, "composite action", raw_lines_of(repo_root / action.path)))
    ci_dir = repo_root / "ci"
    if ci_dir.is_dir():
        for script in sorted(p for p in ci_dir.rglob("*") if p.is_file() and p.suffix in _SCRIPT_SUFFIXES):
            lines = raw_lines_of(script)
            findings.extend(_text_findings("\n".join(lines), script.relative_to(repo_root).as_posix(), "script", lines))
    return findings


def check_gate_012(repo_root: Path) -> list[Finding]:
    return check_coderabbit(repo_root) + check_pr_workflows(repo_root) + check_waits(repo_root)


# ── live: required status checks ───────────────────────────────────────────


@dataclass(frozen=True)
class RequiredCheck:
    context: str
    app_id: int | None
    source: str


def _required_from_protection(body: JsonValue) -> list[RequiredCheck]:
    rsc = body.get("required_status_checks") if isinstance(body, dict) else None
    if not isinstance(rsc, dict):
        return []
    out: list[RequiredCheck] = []
    checks = rsc.get("checks")
    for check in checks if isinstance(checks, list) else []:
        if isinstance(check, dict) and isinstance(check.get("context"), str):
            app_id = check.get("app_id")
            out.append(RequiredCheck(check["context"], app_id if isinstance(app_id, int) else None, "branch protection"))
    known = {c.context for c in out}
    contexts = rsc.get("contexts")
    for context in contexts if isinstance(contexts, list) else []:
        if isinstance(context, str) and context not in known:
            out.append(RequiredCheck(context, None, "branch protection"))
    return out


def _required_from_rulesets(rulesets: JsonValue) -> list[RequiredCheck]:
    out: list[RequiredCheck] = []
    for ruleset in rulesets if isinstance(rulesets, list) else []:
        if not isinstance(ruleset, dict) or ruleset.get("enforcement") != "active":
            continue
        rules = ruleset.get("rules")
        for rule in rules if isinstance(rules, list) else []:
            if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
                continue
            params = rule.get("parameters")
            checks = params.get("required_status_checks") if isinstance(params, dict) else None
            for check in checks if isinstance(checks, list) else []:
                if isinstance(check, dict) and isinstance(check.get("context"), str):
                    app_id = check.get("integration_id")
                    out.append(RequiredCheck(check["context"], app_id if isinstance(app_id, int) else None,
                                             f"ruleset {ruleset.get('name')!r}"))
    return out


def check_required_checks(protection: JsonValue, rulesets: JsonValue, default_branch: str) -> list[Finding]:
    """`protection` is the classic branch-protection body and `rulesets` the
    list of detailed rulesets, each `None` when unreadable (GEN-006/GEN-011
    already report that); this only judges what was read."""

    required = _required_from_protection(protection) + _required_from_rulesets(rulesets)
    findings: list[Finding] = []
    for check in required:
        name = _app_check(check.context)
        foreign_app = check.app_id is not None and check.app_id not in (-1, GITHUB_ACTIONS_APP_ID)
        if name is None and not foreign_app:
            continue
        why = f"names the GitHub App check '{name}'" if name else f"is bound to app id {check.app_id}, not GitHub Actions"
        findings.append(
            Finding(
                rule=RULE, path=f"branch:{default_branch}",
                message=f"required status check {check.context!r} ({check.source}) {why}; act cannot run it",
                fix="remove it from the required status checks; only checks a local bosn -> act run "
                f"reproduces (normally the gate, e.g. 'CI OK') may be required ({POLICY})",
            )
        )
    return findings


# ── CLI ─────────────────────────────────────────────────────────────────────


def _cmd_remote_only(args: argparse.Namespace) -> int:
    findings = check_gate_012(Path(args.repo).resolve())
    if args.json:
        print(json.dumps([
            {"rule": f.rule, "status": f.status.value, "path": f.path, "line": f.line, "message": f.message, "fix": f.fix}
            for f in findings
        ], indent=2))
    else:
        for finding in findings:
            print(finding.render())
    violations = sum(1 for f in findings if f.status == Status.VIOLATION)
    print(f"remote-only: {violations} violation(s), {len(findings) - violations} other finding(s)", file=sys.stderr)
    return 1 if violations else 0


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("remote-only", help="checks act cannot run never gate a PR: CodeRabbit, OIDC, app waits (GATE-012)")
    p.add_argument("--repo", default=".")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_remote_only)
