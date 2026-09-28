"""Group 5: secrets and permissions.

SEC-001 is a raw-text regex scan (secrets can appear inside `env:`, `with:`,
`if:` or `run:` values alike, so matching text directly is both simpler and
more robust than depending on a full YAML parse). SEC-002 needs the parsed
job/permissions structure, so it follows the same PyYAML/yq/needs_review
fallback as the other structural rules.
"""

from __future__ import annotations

import re
from pathlib import Path

from ci_lint.finding import Finding, Status
from ci_lint.workflow_scan import as_dict, jobs_of, load_composite_actions, load_workflows
from ci_lint.yaml_io import LoadStatus, YamlValue

SECRET_REF_RE = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)")
INHERIT_RE = re.compile(r"^\s*secrets:\s*inherit\s*$")


def _discover_raw_files(repo_root: Path) -> list[Path]:
    out: list[Path] = []
    wf_dir = repo_root / ".github" / "workflows"
    if wf_dir.is_dir():
        out.extend(sorted(p for p in wf_dir.iterdir() if p.suffix in (".yml", ".yaml")))
    actions_dir = repo_root / ".github" / "actions"
    if actions_dir.is_dir():
        out.extend(sorted(actions_dir.rglob("action.yml")))
        out.extend(sorted(actions_dir.rglob("action.yaml")))
    for name in ("action.yml", "action.yaml"):
        p = repo_root / name
        if p.is_file():
            out.append(p)
    return out


def check_sec_001(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in _discover_raw_files(repo_root):
        rel = path.relative_to(repo_root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in SECRET_REF_RE.finditer(line):
                name = m.group(1)
                if name != "GITHUB_TOKEN":
                    findings.append(
                        Finding(
                            rule="SEC-001",
                            path=rel,
                            line=lineno,
                            message=f"references secrets.{name}",
                            fix=f"remove the dependency on secrets.{name}; this profile is OIDC-only "
                            "(no repository secrets besides the default secrets.GITHUB_TOKEN)",
                        )
                    )
            if INHERIT_RE.match(line):
                findings.append(
                    Finding(
                        rule="SEC-001",
                        path=rel,
                        line=lineno,
                        message="'secrets: inherit' passes every caller secret into a reusable "
                        "workflow call",
                        fix="list only the specific secrets actually needed (none, in this profile) "
                        "instead of 'secrets: inherit'",
                    )
                )
    return findings


def _permissions_findings(perms: YamlValue, path: str, loc: str) -> list[Finding]:
    if isinstance(perms, str):
        return [
            Finding(
                rule="SEC-002",
                path=path,
                message=f"{loc} = '{perms}' is wider than 'contents: read'",
                fix=f"replace {loc} with an explicit table: 'permissions: {{ contents: read }}'",
            )
        ]
    if not isinstance(perms, dict):
        return []
    findings: list[Finding] = []
    for key, val in perms.items():
        if key == "contents":
            if val != "read":
                findings.append(
                    Finding(
                        rule="SEC-002",
                        path=path,
                        message=f"{loc}.contents = {val!r}, expected 'read'",
                        fix=f"set {loc}.contents = \"read\"",
                    )
                )
        elif key == "id-token":
            if val == "write":
                findings.append(
                    Finding(
                        rule="SEC-002",
                        path=path,
                        message=f"{loc}.id-token = 'write' grants OIDC to every job it applies to",
                        fix="remove 'id-token: write' here; set it only on the 'publish' job's own "
                        "permissions block, alongside 'environment: pypi'",
                    )
                )
        elif val not in (None, "none"):
            findings.append(
                Finding(
                    rule="SEC-002",
                    path=path,
                    message=f"{loc}.{key} = {val!r} is wider than 'contents: read'",
                    fix=f"remove '{key}' from {loc}, or set it to 'none'",
                )
            )
    return findings


def check_sec_002(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for wf in load_workflows(repo_root):
        if wf.status == LoadStatus.NEEDS_REVIEW:
            findings.append(
                Finding(
                    rule="SEC-002",
                    path=wf.path,
                    status=Status.NEEDS_REVIEW,
                    message=f"cannot evaluate SEC-002 for {wf.path}: {wf.reason}",
                    fix="make PyYAML importable or `yq` available on PATH so ci-lint can parse "
                    f"{wf.path}, then re-run precheck",
                )
            )
            continue
        doc = as_dict(wf.document)
        top_perms = doc.get("permissions")
        if top_perms is None:
            findings.append(
                Finding(
                    rule="SEC-002",
                    path=wf.path,
                    message="no top-level 'permissions' block (the default can be wider than "
                    "'contents: read')",
                    fix="add a top-level 'permissions: { contents: read }' to " + wf.path,
                )
            )
        else:
            findings.extend(_permissions_findings(top_perms, wf.path, "permissions"))

        for job_id, job in jobs_of(doc).items():
            job_perms = job.get("permissions")
            if job_perms is None:
                continue
            job_findings = _permissions_findings(job_perms, wf.path, f"jobs.{job_id}.permissions")
            for f in job_findings:
                if "id-token" in f.message:
                    environment = job.get("environment")
                    env_name = environment if isinstance(environment, str) else (
                        environment.get("name") if isinstance(environment, dict) else None
                    )
                    if job_id == "publish" and env_name == "pypi":
                        continue  # the one allowed case
                findings.append(f)
    return findings


def check_group5(repo_root: Path) -> list[Finding]:
    return check_sec_001(repo_root) + check_sec_002(repo_root)
