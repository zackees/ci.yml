"""`python3 -m ci_lint <command> ...` argument parsing and dispatch."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ci_lint.cargo_messages import load_artifacts_file
from ci_lint.cargo_scan import discover_workspace
from ci_lint.finding import Status
from ci_lint.plan import Plan, PlanError, build_act_event, compute_plan
from ci_lint.precheck import (
    has_violations,
    render_json,
    render_text,
    run_precheck,
    write_step_summary,
)
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


def _cmd_precheck(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    event = _load_event(args.event)
    title = args.title if args.title is not None else (_title_from_event(event) or "")

    result = run_precheck(repo_root, title=title, local=args.local)

    if args.json:
        print(render_json(result))
    else:
        print(render_text(result))
    write_step_summary(result)

    if args.plan_out:
        ci, _ = load_ci_toml(repo_root)
        if ci is not None:
            try:
                plan = compute_plan(
                    ci,
                    event_name="pull_request" if title else "push",
                    title=title,
                    dispatch_sha=_sha_from_event(event),
                )
                Path(args.plan_out).write_text(json.dumps(plan.to_json_dict(), indent=2), encoding="utf-8")
            except PlanError as exc:
                print(f"ci-lint plan: {exc}", file=sys.stderr)

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
            ci, event_name=event_name, title=title, ref=args.ref, dispatch_sha=dispatch_sha
        )
    except PlanError as exc:
        print(f"ci-lint plan: {exc}", file=sys.stderr)
        return 1

    payload = plan.to_json_dict()
    text = json.dumps(payload, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)

    if args.act:
        Path(args.act).write_text(json.dumps(build_act_event(title), indent=2), encoding="utf-8")

    if args.github_output:
        gh_out = os.environ.get("GITHUB_OUTPUT")
        if gh_out:
            compact = json.dumps(payload, separators=(",", ":"))
            platforms_json = json.dumps([p.id for p in plan.platforms])
            cross_platforms_json = json.dumps(
                [p.id for p in plan.platforms if p.group != "linux"]
            )
            suites_json = json.dumps(list(plan.suites))
            dylint_targets_json = json.dumps(list(plan.dylint_targets))
            with open(gh_out, "a", encoding="utf-8") as fh:
                fh.write(f"plan={compact}\n")
                fh.write(f"platforms_json={platforms_json}\n")
                fh.write(f"cross_platforms_json={cross_platforms_json}\n")
                fh.write(f"suites_json={suites_json}\n")
                fh.write(f"dylint_targets_json={dylint_targets_json}\n")
                fh.write(f"needs_platform_lanes={'true' if plan.needs_platform_lanes else 'false'}\n")
                fh.write(f"mergeable={'true' if plan.mergeable else 'false'}\n")

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

    report = compute_gate(plan, needs)
    print(json.dumps(gate_to_json_dict(report), indent=2) if args.json else render_gate_text(report))
    if report.not_mergeable_message:
        print(report.not_mergeable_message, file=sys.stderr)
    return 0 if report.ok else 1


def _cmd_selftest(_args: argparse.Namespace) -> int:
    return run_selftest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ci_lint")
    sub = parser.add_subparsers(dest="command", required=True)

    p_precheck = sub.add_parser("precheck", help="run the static precheck")
    p_precheck.add_argument("--repo", default=".")
    p_precheck.add_argument("--local", action="store_true")
    p_precheck.add_argument("--event", default=None)
    p_precheck.add_argument("--title", default=None)
    p_precheck.add_argument("--plan-out", default=None)
    p_precheck.add_argument("--json", action="store_true")
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
    p_gate.add_argument("--json", action="store_true")
    p_gate.set_defaults(func=_cmd_gate)

    p_selftest = sub.add_parser("selftest", help="run ci_lint's own unittest suite")
    p_selftest.set_defaults(func=_cmd_selftest)

    for name in ("cache", "audit"):
        p_stub = sub.add_parser(name, help=f"({name}: not implemented yet -- see docs/ci-toml.md)")
        p_stub.add_argument("args", nargs=argparse.REMAINDER)
        p_stub.set_defaults(func=lambda _args, _name=name: run_stub(_name))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
