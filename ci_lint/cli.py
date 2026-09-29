"""`python3 -m ci_lint <command> ...` argument parsing and dispatch."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import replace
from pathlib import Path

from ci_lint.cache.audit import AuditError, run_audit
from ci_lint.cache.audit import render_text as render_cache_audit_text
from ci_lint.cache.audit import to_json_dict as cache_audit_to_json_dict
from ci_lint.cache.delta import (
    DeltaError,
    StaleBaseError,
    apply as apply_delta,
    build_manifest,
    load_manifest,
    manifest_to_json,
    pack as pack_delta,
)
from ci_lint.cache.keys import CacheKeyError, build_delta_key, build_family_key
from ci_lint.cache.ops import heal as cache_heal
from ci_lint.cache.ops import janitor as cache_janitor
from ci_lint.cache.ops import preprune as cache_preprune
from ci_lint.cache.ops import trim as cache_trim
from ci_lint.cache.save_ok import SaveOkInputError, SaveOkRequest, evaluate_save_ok
from ci_lint.cargo_messages import JsonValue, load_artifacts_file
from ci_lint.cargo_scan import discover_workspace
from ci_lint.finding import Finding, Status
from ci_lint.github_api import default_delete, default_fetch, default_graphql
from ci_lint.plan import Plan, PlanError, build_act_event, compute_plan
from ci_lint.precheck import (
    has_violations,
    render_json,
    render_text,
    run_precheck,
    write_step_summary,
)
from ci_lint.reuse import ReuseResult, compute_reuse, empty_result
from ci_lint.rules.contract import extract_bracket_tokens
from ci_lint.runtime.gate import compute_gate
from ci_lint.runtime.gate import render_text as render_gate_text
from ci_lint.runtime.gate import to_json_dict as gate_to_json_dict
from ci_lint.runtime.tests_size import compute_tests_size
from ci_lint.runtime.tests_size import render_text as render_tests_size_text
from ci_lint.runtime.tests_size import to_json_dict as tests_size_to_json_dict
from ci_lint.runtime.units import compute_units
from ci_lint.runtime.units import render_text as render_units_text
from ci_lint.runtime.units import to_json_dict as units_to_json_dict
from ci_lint.runtime.wheel import check_installed, check_wheel
from ci_lint.runtime.wheel import installed_check_to_json_dict, wheel_check_to_json_dict
from ci_lint.runtime.wheel import render_installed_check, render_wheel_check
from ci_lint.runtime_stub import run_stub
from ci_lint.schema import CiToml, load_ci_toml
from ci_lint.selftest import run_selftest


def _load_event(path: str | None) -> dict[str, object]:
    """Load the GitHub event payload from `--event`, falling back to the
    runner's `GITHUB_EVENT_PATH` (also set by act). Without this fallback a
    workflow step that omitted `--event` silently lost the PR head SHA, and
    title-edit reuse never fired (template-python-rust-cmd#19)."""
    if path is None:
        path = os.environ.get("GITHUB_EVENT_PATH") or None
    if path is None:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _title_from_event(event: dict[str, object]) -> str | None:
    pr = event.get("pull_request")
    if isinstance(pr, dict) and isinstance(pr.get("title"), str):
        return pr["title"]
    return None


def _sha_from_event(event: dict[str, object]) -> str | None:
    inputs = event.get("inputs")
    if isinstance(inputs, dict) and isinstance(inputs.get("sha"), str):
        return inputs["sha"]
    return None


def _head_sha_from_event(event: dict[str, object]) -> str | None:
    pr = event.get("pull_request")
    if isinstance(pr, dict):
        head = pr.get("head")
        if isinstance(head, dict) and isinstance(head.get("sha"), str):
            return head["sha"]
    return None


def _compute_reuse_for_plan(plan: Plan, event_name: str, event: dict[str, object]) -> ReuseResult:
    """Round-3A brief, Part 2. Only ever makes a network call for the
    `pull_request` event, and only when GITHUB_TOKEN, GITHUB_REPOSITORY,
    and the PR's head SHA (from the event payload) are all available --
    otherwise (any other event, or missing credentials/context) returns a
    deterministic "reuse nothing" result with no I/O, so this is always
    network-free in unit tests and on push/schedule/workflow_dispatch."""

    lane_digests = dict(plan.lane_digests)
    if event_name != "pull_request":
        return empty_result(lane_digests)
    token = os.environ.get("GITHUB_TOKEN")
    repo_slug = os.environ.get("GITHUB_REPOSITORY")
    head_sha = _head_sha_from_event(event)
    if not token or not repo_slug:
        return empty_result(lane_digests)
    if not head_sha:
        warning = (
            "reuse: no pull_request.head.sha in the event payload; pass --event or run "
            "inside GitHub Actions (GITHUB_EVENT_PATH) -- reusing nothing"
        )
        print(f"ci-lint: {warning}", file=sys.stderr)
        return empty_result(lane_digests, warning=warning)
    result = compute_reuse(
        repo=repo_slug,
        head_sha=head_sha,
        current_run_id=os.environ.get("GITHUB_RUN_ID"),
        lane_digests=lane_digests,
        fetch=default_fetch,
        token=token,
    )
    if result.warning:
        print(f"ci-lint: {result.warning}", file=sys.stderr)
    return result


def _plan_github_output_lines(plan: Plan, reuse_result: ReuseResult | None) -> list[str]:
    payload = plan.to_json_dict()
    compact = json.dumps(payload, separators=(",", ":"))
    platforms_json = json.dumps([p.id for p in plan.platforms])
    cross_platforms_json = json.dumps([p.id for p in plan.platforms if p.group != "linux"])
    suites_json = json.dumps(list(plan.suites))
    dylint_targets_json = json.dumps(list(plan.dylint_targets))
    platform_lanes_json = json.dumps([pl.to_json_dict() for pl in plan.platform_lanes], separators=(",", ":"))
    fast_suites_json = json.dumps(list(plan.fast_suites), separators=(",", ":"))
    lane_digests_json = json.dumps(dict(plan.lane_digests), separators=(",", ":"))
    # Round-4B: derived straight from cache_mode so a writer flow's steps
    # (setup-soldr's/astral-sh/setup-uv's 'save-cache' inputs) can pass this
    # straight through as their plan-driven expression -- see
    # ci_lint.rules.tools._is_plan_expr / ci_lint.rules.cache_static
    # .check_cache_003_setup_uv.
    cache_save = "true" if plan.cache_mode == "write" else "false"

    lines = [
        f"plan={compact}",
        f"platforms_json={platforms_json}",
        f"cross_platforms_json={cross_platforms_json}",
        f"suites_json={suites_json}",
        f"dylint_targets_json={dylint_targets_json}",
        f"needs_platform_lanes={'true' if plan.needs_platform_lanes else 'false'}",
        f"mergeable={'true' if plan.mergeable else 'false'}",
        # Round-3A brief, Part 1.
        f"platform_lanes_json={platform_lanes_json}",
        f"fast_suites_json={fast_suites_json}",
        f"lane_digests_json={lane_digests_json}",
        # Round-4B.
        f"cache_save={cache_save}",
    ]

    if reuse_result is not None:
        reused_platform_ids = {
            key.split(":", 1)[1]
            for key, entry in reuse_result.reuse.items()
            if key.startswith("platform:") and entry is not None
        }
        platform_lanes_todo = [
            pl.to_json_dict() for pl in plan.platform_lanes if pl.id not in reused_platform_ids
        ]
        reuse_json = json.dumps(reuse_result.to_json_dict(), separators=(",", ":"))
        platform_lanes_todo_json = json.dumps(platform_lanes_todo, separators=(",", ":"))
        fast_reused = reuse_result.reuse.get("fast") is not None
        dylint_reused = reuse_result.reuse.get("dylint") is not None
        lines.extend(
            [
                # Round-3A brief, Part 2.
                f"reuse_json={reuse_json}",
                f"platform_lanes_todo_json={platform_lanes_todo_json}",
                f"fast_reused={'true' if fast_reused else 'false'}",
                f"dylint_reused={'true' if dylint_reused else 'false'}",
            ]
        )

    return lines


def _write_plan_github_output(plan: Plan, reuse_result: ReuseResult | None) -> None:
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if not gh_out:
        return
    with open(gh_out, "a", encoding="utf-8") as fh:
        for line in _plan_github_output_lines(plan, reuse_result):
            fh.write(line + "\n")


def _live_cache_findings(ci: CiToml | None, *, default_branch: str) -> list[Finding]:
    """Round-4A brief, deliverable 4: `precheck --live` runs `ci_lint.cache
    .audit` and folds its findings in as WARNINGS only (never fails a PR
    for cache state it didn't cause) -- except CACHE-009 (a live retired-
    family entry), which fails. This is the one place `ci_lint.precheck`'s
    otherwise network-free rule groups gain a GitHub API dependency, kept
    out of `ci_lint.precheck.run_precheck` itself so that module stays
    testable with no network, matching `_compute_reuse_for_plan` above."""

    if ci is None:
        return []
    token = os.environ.get("GITHUB_TOKEN")
    repo_slug = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo_slug:
        return [
            Finding(
                rule="CACHE-005",
                status=Status.NEEDS_REVIEW,
                message="skipped (--live): GITHUB_TOKEN/GITHUB_REPOSITORY not set",
                fix="run with GITHUB_TOKEN and GITHUB_REPOSITORY set (actions: read) to cover the "
                "live cache audit; it is never treated as passing without them",
            )
        ]
    try:
        report = run_audit(
            ci, fetch=default_fetch, graphql=default_graphql, token=token, repo=repo_slug,
            default_branch=default_branch,
        )
    except AuditError as exc:
        return [
            Finding(
                rule="CACHE-005",
                status=Status.NEEDS_REVIEW,
                message=f"skipped (--live): live cache audit failed: {exc}",
                fix="re-run once the GitHub Actions cache API is reachable; this is never treated as "
                "passing on failure",
            )
        ]
    out: list[Finding] = []
    for f in report.findings:
        # CACHE-009 (a retired family actually present in the live cache
        # account) fails; everything else the live audit can find is a
        # warning -- issue #6 §6: "It never blames a PR for cache state
        # the PR didn't cause."
        out.append(f if f.rule == "CACHE-009" else replace(f, status=Status.NEEDS_REVIEW))
    if report.warning:
        out.append(
            Finding(
                rule="CACHE-008",
                status=Status.NEEDS_REVIEW,
                message=f"--live: {report.warning}",
                fix="re-run once the GitHub API is reachable (PR-state GraphQL lookup failed; the "
                "rest of the live audit still ran)",
            )
        )
    return out


def _cmd_precheck(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    event = _load_event(args.event)
    title = args.title if args.title is not None else (_title_from_event(event) or "")

    result = run_precheck(repo_root, title=title, local=args.local, live=args.live)
    if args.live:
        extra = _live_cache_findings(result.ci, default_branch=args.default_branch)
        result = replace(result, findings=result.findings + tuple(extra))

    if args.json:
        print(render_json(result))
    else:
        print(render_text(result))
    write_step_summary(result)

    if args.plan_out or args.github_output:
        ci, _ = load_ci_toml(repo_root)
        if ci is not None:
            event_name = "pull_request" if title else "push"
            try:
                plan = compute_plan(
                    ci,
                    event_name=event_name,
                    title=title,
                    dispatch_sha=_sha_from_event(event),
                    repo_root=repo_root,
                )
            except PlanError as exc:
                print(f"ci-lint plan: {exc}", file=sys.stderr)
            else:
                reuse_result = _compute_reuse_for_plan(plan, event_name, event) if args.reuse else None
                if args.plan_out:
                    payload = plan.to_json_dict()
                    if reuse_result is not None:
                        payload["reuse"] = reuse_result.to_json_dict()
                    Path(args.plan_out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
                if args.github_output:
                    _write_plan_github_output(plan, reuse_result)

    return 1 if has_violations(result) else 0


def _cmd_plan(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    ci, findings = load_ci_toml(repo_root)
    if ci is None:
        for f in findings:
            print(f.render())
        print("ci-lint plan: cannot load ci.toml", file=sys.stderr)
        return 1

    event = _load_event(args.event)
    event_name = args.event_name or os.environ.get("GITHUB_EVENT_NAME")
    if event_name is None:
        print("ci-lint plan: --event-name is required (or set GITHUB_EVENT_NAME)", file=sys.stderr)
        return 1
    title = args.title if args.title is not None else (_title_from_event(event) or "")
    dispatch_sha = args.sha or _sha_from_event(event)

    try:
        plan = compute_plan(
            ci,
            event_name=event_name,
            title=title,
            ref=args.ref,
            dispatch_sha=dispatch_sha,
            repo_root=repo_root,
        )
    except PlanError as exc:
        print(f"ci-lint plan: {exc}", file=sys.stderr)
        return 1

    reuse_result = _compute_reuse_for_plan(plan, event_name, event) if args.reuse else None

    payload = plan.to_json_dict()
    if reuse_result is not None:
        payload["reuse"] = reuse_result.to_json_dict()
    text = json.dumps(payload, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)

    if args.act:
        Path(args.act).write_text(json.dumps(build_act_event(title), indent=2), encoding="utf-8")

    if args.github_output:
        _write_plan_github_output(plan, reuse_result)

    return 0


def _load_ci_or_die(repo_root: Path, command: str) -> CiToml | None:
    ci, findings = load_ci_toml(repo_root)
    if ci is None:
        for f in findings:
            print(f.render())
        print(f"ci-lint {command}: cannot load ci.toml", file=sys.stderr)
    return ci


def _cmd_units(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    ci = _load_ci_or_die(repo_root, "units")
    if ci is None:
        return 1
    crates = discover_workspace(repo_root)
    artifacts = load_artifacts_file(Path(args.artifacts))
    report = compute_units(ci, crates, artifacts)
    print(json.dumps(units_to_json_dict(report), indent=2) if args.json else render_units_text(report))
    return 1 if any(f.status == Status.VIOLATION for f in report.findings) else 0


def _cmd_tests_size(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    ci = _load_ci_or_die(repo_root, "tests size")
    if ci is None:
        return 1
    artifacts = load_artifacts_file(Path(args.artifacts))
    report = compute_tests_size(ci, artifacts)
    print(json.dumps(tests_size_to_json_dict(report), indent=2) if args.json else render_tests_size_text(report))
    return 1 if any(f.status == Status.VIOLATION for f in report.findings) else 0


def _cmd_wheel_check(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    ci = _load_ci_or_die(repo_root, "wheel check")
    if ci is None:
        return 1
    sdist_path = Path(args.sdist) if args.sdist else None
    report = check_wheel(ci, Path(args.wheel), sdist_path)
    print(json.dumps(wheel_check_to_json_dict(report), indent=2) if args.json else render_wheel_check(report))
    return 1 if any(f.status == Status.VIOLATION for f in report.findings) else 0


def _cmd_wheel_installed(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    ci = _load_ci_or_die(repo_root, "wheel installed")
    if ci is None:
        return 1
    report = check_installed(ci, repo_root, Path(args.venv))
    print(
        json.dumps(installed_check_to_json_dict(report), indent=2)
        if args.json
        else render_installed_check(report)
    )
    return 1 if any(f.status == Status.VIOLATION for f in report.findings) else 0


def _cmd_gate(args: argparse.Namespace) -> int:
    try:
        with open(args.plan, encoding="utf-8") as fh:
            plan = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ci-lint gate: cannot read --plan {args.plan!r}: {exc}", file=sys.stderr)
        return 1
    try:
        with open(args.needs, encoding="utf-8") as fh:
            needs = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ci-lint gate: cannot read --needs {args.needs!r}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(plan, dict) or not isinstance(needs, dict):
        print("ci-lint gate: --plan and --needs must both be JSON objects", file=sys.stderr)
        return 1

    reuse: dict[str, JsonValue] | None = None
    if args.reuse:
        try:
            with open(args.reuse, encoding="utf-8") as fh:
                reuse_raw = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ci-lint gate: cannot read --reuse {args.reuse!r}: {exc}", file=sys.stderr)
            return 1
        if not isinstance(reuse_raw, dict):
            print("ci-lint gate: --reuse must be a JSON object", file=sys.stderr)
            return 1
        reuse = reuse_raw

    event = _load_event(args.event)
    head_sha = _head_sha_from_event(event)
    token = os.environ.get("GITHUB_TOKEN")
    repo_slug = os.environ.get("GITHUB_REPOSITORY")
    fetch = default_fetch if token and repo_slug else None

    report = compute_gate(
        plan, needs, reuse=reuse, head_sha=head_sha, fetch=fetch, token=token, repo=repo_slug
    )
    print(json.dumps(gate_to_json_dict(report), indent=2) if args.json else render_gate_text(report))
    if report.not_mergeable_message:
        print(report.not_mergeable_message, file=sys.stderr)
    return 0 if report.ok else 1


def _cmd_selftest(_args: argparse.Namespace) -> int:
    return run_selftest()


# ── `ci-lint cache ...` (round-4A) ──────────────────────────────────────────


def _parse_tags_arg(raw: str | None) -> tuple[str, ...]:
    """`--tags` accepts either a PR-title-shaped string (`"[ci-cache-save]
    [ci-full]"`) -- reusing the exact same bracket extraction TAG-001/002
    and the planner use, so a caller can pass the real PR title verbatim --
    or a bare space/comma-separated list (`"ci-cache-save"`)."""

    if not raw:
        return ()
    bracketed = extract_bracket_tokens(raw)
    if bracketed:
        return tuple(bracketed)
    return tuple(t for t in re.split(r"[\s,]+", raw.strip()) if t)


def _cmd_cache_key(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    ci = _load_ci_or_die(repo_root, "cache key")
    if ci is None:
        return 1
    try:
        if args.pr is not None:
            if not args.base_key:
                print("ci-lint cache key: --pr requires --base-key", file=sys.stderr)
                return 2
            if not args.platform:
                print("ci-lint cache key: --pr requires --platform", file=sys.stderr)
                return 2
            key = build_delta_key(
                family_id=args.family, platform_id=args.platform, pr=args.pr, base_key=args.base_key
            )
        else:
            key = build_family_key(ci, args.family, platform_id=args.platform, repo_root=repo_root).key
    except CacheKeyError as exc:
        print(f"ci-lint cache key: {exc}", file=sys.stderr)
        return 2
    print(key)
    return 0


def _cmd_cache_save_ok(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    ci = _load_ci_or_die(repo_root, "cache save-ok")
    if ci is None:
        return 1
    req = SaveOkRequest(
        family=args.family,
        flow=args.flow,
        event=args.event,
        pr=args.pr,
        fork=args.fork,
        act=args.act,
        build_failed=args.build_failed,
        exact_hit=args.exact_hit,
        new_units=args.new_units,
        payload_bytes=args.payload_bytes,
        base_key=args.base_key,
        current_base_key=args.current_base_key,
        lockfile_changed=args.lockfile_changed,
        tags=_parse_tags_arg(args.tags),
        rerun_saved=args.rerun_saved,
    )
    try:
        result = evaluate_save_ok(ci, req)
    except SaveOkInputError as exc:
        print(f"ci-lint cache save-ok: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_json_dict(), indent=2) if args.json else result.render())
    return 0


def _cache_write_creds(command: str) -> tuple[str, str] | None:
    token = os.environ.get("GITHUB_TOKEN")
    repo_slug = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo_slug:
        print(f"ci-lint cache {command}: GITHUB_TOKEN and GITHUB_REPOSITORY are required", file=sys.stderr)
        return None
    return token, repo_slug


def _cmd_cache_audit(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    ci = _load_ci_or_die(repo_root, "cache audit")
    if ci is None:
        return 1
    creds = _cache_write_creds("audit")  # "write" is a misnomer here -- audit only needs actions: read
    if creds is None:
        return 1
    token, repo_slug = creds
    try:
        report = run_audit(
            ci, fetch=default_fetch, graphql=default_graphql, token=token, repo=repo_slug,
            default_branch=args.default_branch,
        )
    except AuditError as exc:
        print(f"ci-lint cache audit: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(cache_audit_to_json_dict(report), indent=2) if args.json else render_cache_audit_text(report))
    return 1 if any(f.status == Status.VIOLATION for f in report.findings) else 0


def _cmd_cache_trim(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    ci = _load_ci_or_die(repo_root, "cache trim")
    if ci is None:
        return 1
    creds = _cache_write_creds("trim")
    if creds is None:
        return 1
    token, repo_slug = creds
    delete_fn = None if args.dry_run else default_delete
    result = cache_trim(
        ci, fetch=default_fetch, graphql=default_graphql, delete=delete_fn, token=token, repo=repo_slug,
        max_deletes=args.max_deletes, dry_run=args.dry_run, default_branch=args.default_branch,
    )
    print(json.dumps(result.to_json_dict(), indent=2) if args.json else result.render())
    return 1 if result.errors else 0


def _cmd_cache_janitor(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    ci = _load_ci_or_die(repo_root, "cache janitor")
    if ci is None:
        return 1
    creds = _cache_write_creds("janitor")
    if creds is None:
        return 1
    token, repo_slug = creds
    delete_fn = None if args.dry_run else default_delete
    result, table = cache_janitor(
        ci, fetch=default_fetch, graphql=default_graphql, delete=delete_fn, token=token, repo=repo_slug,
        max_deletes=args.max_deletes, dry_run=args.dry_run, stale_days=args.stale_days,
        default_branch=args.default_branch,
    )
    if args.json:
        payload = result.to_json_dict()
        payload["table"] = table
        print(json.dumps(payload, indent=2))
    else:
        print(result.render())
        print(table)
    return 1 if result.errors else 0


def _cmd_cache_heal(args: argparse.Namespace) -> int:
    creds = _cache_write_creds("heal")
    if creds is None:
        return 1
    token, repo_slug = creds
    delete_fn = None if args.dry_run else default_delete
    result = cache_heal(delete=delete_fn, token=token, repo=repo_slug, key=args.key, ref=args.ref, dry_run=args.dry_run)
    print(json.dumps(result.to_json_dict(), indent=2) if args.json else result.render())
    return 1 if result.errors else 0


def _cmd_cache_preprune(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    ci = _load_ci_or_die(repo_root, "cache preprune")
    if ci is None:
        return 1
    creds = _cache_write_creds("preprune")
    if creds is None:
        return 1
    token, repo_slug = creds
    delete_fn = None if args.dry_run else default_delete
    result, summary = cache_preprune(
        ci, fetch=default_fetch, graphql=default_graphql, delete=delete_fn, token=token, repo=repo_slug,
        lockfile_changed=args.lockfile_changed, max_deletes=args.max_deletes, dry_run=args.dry_run,
        default_branch=args.default_branch,
    )
    if args.json:
        payload = result.to_json_dict()
        payload["summary"] = summary
        print(json.dumps(payload, indent=2))
    else:
        print(summary)
        print(result.render())
    return 1 if result.errors else 0


def _cmd_cache_delta_manifest(args: argparse.Namespace) -> int:
    try:
        manifest = build_manifest(Path(args.dir))
    except DeltaError as exc:
        print(f"ci-lint cache delta manifest: {exc}", file=sys.stderr)
        return 2
    text = manifest_to_json(manifest)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


def _cmd_cache_delta_pack(args: argparse.Namespace) -> int:
    try:
        base = load_manifest(Path(args.base_manifest))
        changed = pack_delta(
            Path(args.dir), base, Path(args.out), family=args.family, platform=args.platform, pr=args.pr
        )
    except DeltaError as exc:
        print(f"ci-lint cache delta pack: {exc}", file=sys.stderr)
        return 2
    print(f"ci-lint cache delta pack: packed {len(changed)} file(s) into {args.out}")
    return 0


def _cmd_cache_delta_apply(args: argparse.Namespace) -> int:
    try:
        base = load_manifest(Path(args.base_manifest))
    except DeltaError as exc:
        print(f"ci-lint cache delta apply: {exc}", file=sys.stderr)
        return 2
    try:
        extracted = apply_delta(Path(args.dir), Path(args.delta), base)
    except StaleBaseError as exc:
        print(f"ci-lint cache delta apply: {exc}", file=sys.stderr)
        return 3
    except DeltaError as exc:
        print(f"ci-lint cache delta apply: {exc}", file=sys.stderr)
        return 2
    print(f"ci-lint cache delta apply: applied {len(extracted)} file(s) from {args.delta}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ci_lint")
    sub = parser.add_subparsers(dest="command", required=True)

    p_precheck = sub.add_parser("precheck", help="run the static precheck")
    p_precheck.add_argument("--repo", default=".")
    p_precheck.add_argument("--local", action="store_true")
    p_precheck.add_argument("--event", default=None)
    p_precheck.add_argument("--title", default=None)
    p_precheck.add_argument("--plan-out", default=None)
    p_precheck.add_argument(
        "--github-output",
        action="store_true",
        help="also write the plan's $GITHUB_OUTPUT keys (same as 'ci_lint plan --github-output')",
    )
    p_precheck.add_argument(
        "--reuse",
        action="store_true",
        help="compute title-edit reuse (round-3A Part 2) and include it in --plan-out/--github-output",
    )
    p_precheck.add_argument("--json", action="store_true")
    p_precheck.add_argument(
        "--live",
        action="store_true",
        help="also run the live cache audit (round-4A; ci_lint.cache.audit) and fold its findings in "
        "as warnings, except CACHE-009 which fails; requires GITHUB_TOKEN + GITHUB_REPOSITORY "
        "(actions: read), else reports a single needs_review instead of silently skipping",
    )
    p_precheck.add_argument(
        "--default-branch",
        default="main",
        help="the branch a base cache layer may legitimately be written from (--live's CACHE-003 check)",
    )
    p_precheck.set_defaults(func=_cmd_precheck)

    p_plan = sub.add_parser("plan", help="compute the flow/tag selection")
    p_plan.add_argument("--repo", default=".")
    p_plan.add_argument("--event-name", default=None)
    p_plan.add_argument("--title", default=None)
    p_plan.add_argument("--ref", default=None)
    p_plan.add_argument("--event", default=None)
    p_plan.add_argument("--out", default=None)
    p_plan.add_argument("--github-output", action="store_true")
    p_plan.add_argument("--act", default=None)
    p_plan.add_argument("--sha", default=None)
    p_plan.add_argument(
        "--reuse",
        action="store_true",
        help="look up title-edit reuse for pull_request events (round-3A Part 2); "
        "requires GITHUB_TOKEN + GITHUB_REPOSITORY and the event's PR head SHA, else reuses nothing",
    )
    p_plan.set_defaults(func=_cmd_plan)

    p_units = sub.add_parser("units", help="compile-unit count + RUST-011 from a cargo --message-format=json run")
    p_units.add_argument("--repo", default=".")
    p_units.add_argument("--artifacts", required=True)
    p_units.add_argument("--json", action="store_true")
    p_units.set_defaults(func=_cmd_units)

    p_tests = sub.add_parser("tests", help="test-binary checks")
    tests_sub = p_tests.add_subparsers(dest="tests_command", required=True)
    p_tests_size = tests_sub.add_parser(
        "size", help="test-binary declaration + size budget (RUST-012) from a cargo --message-format=json run"
    )
    p_tests_size.add_argument("--repo", default=".")
    p_tests_size.add_argument("--artifacts", required=True)
    p_tests_size.add_argument("--json", action="store_true")
    p_tests_size.set_defaults(func=_cmd_tests_size)

    p_wheel = sub.add_parser("wheel", help="wheel packaging checks (PKG-003/004/005)")
    wheel_sub = p_wheel.add_subparsers(dest="wheel_command", required=True)
    p_wheel_check = wheel_sub.add_parser("check", help="check a built wheel (+ optional sdist) by its own bytes")
    p_wheel_check.add_argument("--repo", default=".")
    p_wheel_check.add_argument("--wheel", required=True)
    p_wheel_check.add_argument("--sdist", default=None)
    p_wheel_check.add_argument("--json", action="store_true")
    p_wheel_check.set_defaults(func=_cmd_wheel_check)

    p_wheel_installed = wheel_sub.add_parser("installed", help="check a wheel installed into a venv")
    p_wheel_installed.add_argument("--repo", default=".")
    p_wheel_installed.add_argument("--venv", required=True)
    p_wheel_installed.add_argument("--json", action="store_true")
    p_wheel_installed.set_defaults(func=_cmd_wheel_installed)

    p_gate = sub.add_parser("gate", help="CI OK aggregator: plan.json's required_jobs vs needs.json")
    p_gate.add_argument("--repo", default=".")
    p_gate.add_argument("--plan", required=True)
    p_gate.add_argument("--needs", required=True)
    p_gate.add_argument(
        "--reuse",
        default=None,
        help="path to the plan's reuse map JSON (round-3A Part 3; from "
        "'ci_lint plan --reuse's reuse_json/--out \"reuse\" key)",
    )
    p_gate.add_argument(
        "--event",
        default=None,
        help="pull_request event JSON, to read the PR head SHA for reuse re-verification",
    )
    p_gate.add_argument("--json", action="store_true")
    p_gate.set_defaults(func=_cmd_gate)

    p_selftest = sub.add_parser("selftest", help="run ci_lint's own unittest suite")
    p_selftest.set_defaults(func=_cmd_selftest)

    p_cache = sub.add_parser("cache", help="cache runtime: key, save-ok, audit, trim, janitor, heal, preprune, delta")
    cache_sub = p_cache.add_subparsers(dest="cache_command", required=True)

    p_cache_key = cache_sub.add_parser("key", help="build a cache key for a declared family, or a PR delta key")
    p_cache_key.add_argument("family")
    p_cache_key.add_argument("--repo", default=".")
    p_cache_key.add_argument("--platform", default=None)
    p_cache_key.add_argument("--pr", type=int, default=None)
    p_cache_key.add_argument("--base-key", default=None, help="required with --pr: the restored base's literal key")
    p_cache_key.set_defaults(func=_cmd_cache_key)

    p_cache_save_ok = cache_sub.add_parser("save-ok", help="evaluate issue #6 §6's do-not-save table (CACHE-008)")
    p_cache_save_ok.add_argument("family")
    p_cache_save_ok.add_argument("--repo", default=".")
    p_cache_save_ok.add_argument("--flow", required=True)
    p_cache_save_ok.add_argument("--event", required=True)
    p_cache_save_ok.add_argument("--pr", type=int, default=None)
    p_cache_save_ok.add_argument("--fork", action="store_true")
    p_cache_save_ok.add_argument("--act", action="store_true")
    build_group = p_cache_save_ok.add_mutually_exclusive_group()
    build_group.add_argument("--build-ok", dest="build_failed", action="store_false")
    build_group.add_argument("--build-failed", dest="build_failed", action="store_true")
    p_cache_save_ok.set_defaults(build_failed=False)
    p_cache_save_ok.add_argument("--exact-hit", action="store_true")
    p_cache_save_ok.add_argument("--new-units", type=int, default=None)
    p_cache_save_ok.add_argument("--payload-bytes", type=int, default=None)
    p_cache_save_ok.add_argument("--base-key", default=None)
    p_cache_save_ok.add_argument("--current-base-key", default=None)
    p_cache_save_ok.add_argument("--lockfile-changed", action="store_true")
    p_cache_save_ok.add_argument("--tags", default=None)
    p_cache_save_ok.add_argument("--rerun-saved", action="store_true")
    p_cache_save_ok.add_argument("--json", action="store_true")
    p_cache_save_ok.set_defaults(func=_cmd_cache_save_ok)

    p_cache_audit = cache_sub.add_parser("audit", help="live, read-only classification of every cache entry")
    p_cache_audit.add_argument("--repo", default=".")
    p_cache_audit.add_argument("--default-branch", default="main")
    p_cache_audit.add_argument("--json", action="store_true")
    p_cache_audit.set_defaults(func=_cmd_cache_audit)

    p_cache_trim = cache_sub.add_parser("trim", help="delete CACHE-008 entries (closed/merged + stale-base PR deltas)")
    p_cache_trim.add_argument("--repo", default=".")
    p_cache_trim.add_argument("--default-branch", default="main")
    p_cache_trim.add_argument("--max-deletes", type=int, default=50)
    p_cache_trim.add_argument("--dry-run", action="store_true")
    p_cache_trim.add_argument("--json", action="store_true")
    p_cache_trim.set_defaults(func=_cmd_cache_trim)

    p_cache_janitor = cache_sub.add_parser(
        "janitor", help="delete CACHE-001/003/005/006/009 + stale entries; prints a before/after table"
    )
    p_cache_janitor.add_argument("--repo", default=".")
    p_cache_janitor.add_argument("--default-branch", default="main")
    p_cache_janitor.add_argument("--max-deletes", type=int, default=100)
    p_cache_janitor.add_argument("--stale-days", type=int, default=5)
    p_cache_janitor.add_argument("--dry-run", action="store_true")
    p_cache_janitor.add_argument("--json", action="store_true")
    p_cache_janitor.set_defaults(func=_cmd_cache_janitor)

    p_cache_heal = cache_sub.add_parser("heal", help="delete exactly one cache key (a writer flow's self-heal)")
    p_cache_heal.add_argument("--repo", default=".")
    p_cache_heal.add_argument("--key", required=True)
    p_cache_heal.add_argument("--ref", default=None)
    p_cache_heal.add_argument("--dry-run", action="store_true")
    p_cache_heal.add_argument("--json", action="store_true")
    p_cache_heal.set_defaults(func=_cmd_cache_heal)

    p_cache_preprune = cache_sub.add_parser("preprune", help="forecast the lockfile-change peak with live sizes; prune if over budget")
    p_cache_preprune.add_argument("--repo", default=".")
    p_cache_preprune.add_argument("--default-branch", default="main")
    p_cache_preprune.add_argument("--lockfile-changed", action="store_true")
    p_cache_preprune.add_argument("--max-deletes", type=int, default=50)
    p_cache_preprune.add_argument("--dry-run", action="store_true")
    p_cache_preprune.add_argument("--json", action="store_true")
    p_cache_preprune.set_defaults(func=_cmd_cache_preprune)

    p_cache_delta = cache_sub.add_parser("delta", help="PR delta mechanics: manifest, pack, apply (pure filesystem)")
    delta_sub = p_cache_delta.add_subparsers(dest="cache_delta_command", required=True)

    p_delta_manifest = delta_sub.add_parser("manifest", help="sorted (relpath, size) list + sha256 digest of --dir")
    p_delta_manifest.add_argument("--dir", required=True)
    p_delta_manifest.add_argument("--out", required=True)
    p_delta_manifest.set_defaults(func=_cmd_cache_delta_manifest)

    p_delta_pack = delta_sub.add_parser("pack", help="pack files absent from/changed vs --base-manifest into --out")
    p_delta_pack.add_argument("--dir", required=True)
    p_delta_pack.add_argument("--base-manifest", required=True)
    p_delta_pack.add_argument("--out", required=True)
    p_delta_pack.add_argument("--family", required=True)
    p_delta_pack.add_argument("--platform", required=True)
    p_delta_pack.add_argument("--pr", type=int, required=True)
    p_delta_pack.set_defaults(func=_cmd_cache_delta_pack)

    p_delta_apply = delta_sub.add_parser("apply", help="apply --delta onto --dir; exit 3 if its base is stale")
    p_delta_apply.add_argument("--dir", required=True)
    p_delta_apply.add_argument("--delta", required=True)
    p_delta_apply.add_argument("--base-manifest", required=True)
    p_delta_apply.set_defaults(func=_cmd_cache_delta_apply)

    p_audit_stub = sub.add_parser("audit", help="(audit: not implemented yet -- see docs/ci-toml.md)")
    p_audit_stub.add_argument("args", nargs=argparse.REMAINDER)
    p_audit_stub.set_defaults(func=lambda _args: run_stub("audit"))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
