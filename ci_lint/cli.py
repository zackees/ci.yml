"""`python3 -m ci_lint <command> ...` argument parsing and dispatch."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ci_lint.plan import Plan, PlanError, build_act_event, compute_plan
from ci_lint.precheck import (
    has_violations,
    render_json,
    render_text,
    run_precheck,
    write_step_summary,
)
from ci_lint.runtime_stub import run_stub
from ci_lint.schema import load_ci_toml


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

    result = run_precheck(repo_root, title=title)

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
            with open(gh_out, "a", encoding="utf-8") as fh:
                fh.write(f"plan={compact}\n")
                fh.write(f"platforms_json={platforms_json}\n")
                fh.write(f"cross_platforms_json={cross_platforms_json}\n")
                fh.write(f"suites_json={suites_json}\n")
                fh.write(f"mergeable={'true' if plan.mergeable else 'false'}\n")

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

    for name in ("units", "tests", "cache", "audit"):
        p_stub = sub.add_parser(name, help=f"({name}: not implemented in round 1)")
        p_stub.add_argument("args", nargs=argparse.REMAINDER)
        p_stub.set_defaults(func=lambda _args, _name=name: run_stub(_name))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
