"""`ci-lint attest ...` -- ci-attestations on the host and in CI (GATE-010, #198).

    attest verify   [--commit HEAD] [--base REV]   check a commit's Ci-Attestation trailers
    attest lineage  [--commit X] [--pr N]           print the commit's lineage label
    attest keys     --pr N --out-dir D              tiny side files + cache keys for each valid gate
    attest resolve  --family F [--pr N]             nearest ancestor cache entry to hydrate

`keys` and `resolve` are the caching half: a CI step saves each valid
attestation as its own tiny cache entry (`att1-<gate>-<lineage>`), and a
later run lists entries through the REST API (GET only) to choose which
build cache is valid to hydrate -- the nearest ancestor commit whose
required gates were attested.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from pathlib import Path

from ci_lint.attestations import (
    MISSING,
    VALID,
    Definition,
    load_definition,
    parse_trailers,
    verify_commit,
)
from ci_lint.cache_lineage import Lineage, LineageError, cache_key, lineage_of, parse_key, resolve
from ci_lint.github_api import GitHubApiError, default_fetch
from ci_lint.proc import run_captured

SIDE_FAMILY = "att1"


def gate_family(gate: str, prefix: str = SIDE_FAMILY) -> str:
    """`rust/x86_64-unknown-linux-gnu/test` -> `att1-rust.x86_64-unknown-linux-gnu.test`."""

    return f"{prefix}-{gate.replace('/', '.')}"


def _write_outputs(lines: list[str]) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def _definition(repo: Path, base: str | None) -> Definition | None:
    loaded = load_definition(repo, rev=base)
    if loaded is None:
        print("ci-lint attest: no ci-attestations.yml" + (f" at {base}" if base else ""), file=sys.stderr)
        return None
    for finding in loaded.findings:
        print(finding.render(), file=sys.stderr)
    return loaded.definition


def _lineage(repo: Path, args: argparse.Namespace) -> Lineage:
    return lineage_of(repo, args.commit, main_ref=args.main_ref, pr=args.pr)


def _cmd_verify(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    definition = _definition(repo, args.base)
    if definition is None:
        return 2
    result = verify_commit(repo, args.commit, definition)
    for status in sorted(result.statuses, key=lambda s: s.gate):
        mark = {VALID: "ok  ", MISSING: "--  "}.get(status.state, "FAIL")
        print(f"{mark} {status.gate}: {status.state} -- {status.detail}")
    for error in result.malformed:
        print(f"FAIL {error}")
    return 1 if result.errors or result.malformed else 0


def _cmd_lineage(args: argparse.Namespace) -> int:
    try:
        print(_lineage(Path(args.repo).resolve(), args).label())
    except LineageError as exc:
        print(f"ci-lint attest lineage: {exc}", file=sys.stderr)
        return 2
    return 0


def _cmd_keys(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    definition = _definition(repo, args.base)
    if definition is None:
        return 0  # nothing to publish is not an error
    try:
        lineage = _lineage(repo, args)
    except LineageError as exc:
        print(f"ci-lint attest keys: {exc}", file=sys.stderr)
        return 2
    result = verify_commit(repo, args.commit, definition)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    message = run_captured(["git", "-C", str(repo), "log", "-1", "--format=%B", args.commit]).stdout
    records = {p.attestation.gate: p.attestation for p in parse_trailers(message) if p.attestation is not None}
    lines: list[str] = []
    index = 0
    for status in sorted(result.statuses, key=lambda s: s.gate):
        if status.state != VALID:
            continue
        if index >= args.slots:
            print(f"ci-lint attest keys: more than {args.slots} valid gates; raise --slots", file=sys.stderr)
            break
        path = out_dir / f"{index}.json"
        path.write_text(records[status.gate].compact() + "\n", encoding="utf-8")
        key = cache_key(gate_family(status.gate), lineage)
        stem = f"{gate_family(status.gate)}-{lineage.stem()}"
        print(f"{key}  <- {path}")
        lines += [f"key_{index}={key}", f"stem_{index}={stem}", f"path_{index}={path}"]
        index += 1
    lines.append(f"count={index}")
    lines.append(f"lineage={lineage.label()}")
    if args.github_output:
        _write_outputs(lines)
    return 0


def _list_keys(repo_slug: str, prefix: str, token: str) -> list[str]:
    keys: list[str] = []
    for page in range(1, 11):
        query = urllib.parse.urlencode({"key": prefix, "per_page": 100, "page": page})
        doc = default_fetch(f"https://api.github.com/repos/{repo_slug}/actions/caches?{query}", token)
        caches = doc.get("actions_caches") if isinstance(doc, dict) else None
        if not isinstance(caches, list) or not caches:
            break
        keys += [c["key"] for c in caches if isinstance(c, dict) and isinstance(c.get("key"), str)]
        if len(caches) < 100:
            break
    return keys


def _cmd_resolve(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    try:
        head = _lineage(repo, args)
    except LineageError as exc:
        print(f"ci-lint attest resolve: {exc}", file=sys.stderr)
        return 2
    if args.keys_file:
        keys = [k.strip() for k in Path(args.keys_file).read_text(encoding="utf-8").splitlines() if k.strip()]
    else:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        slug = args.github_repo or os.environ.get("GITHUB_REPOSITORY")
        if not token or not slug:
            print("ci-lint attest resolve: needs --keys-file, or GITHUB_TOKEN and GITHUB_REPOSITORY", file=sys.stderr)
            return 2
        try:
            keys = _list_keys(slug, args.family, token)
            for gate in args.require_gate:
                keys += _list_keys(slug, gate_family(gate), token)
        except GitHubApiError as exc:
            print(f"ci-lint attest resolve: {exc}", file=sys.stderr)
            return 2
    required: frozenset[str] | None = None
    for gate in args.require_gate:
        shas = frozenset(e.lineage.sha for e in (parse_key(k) for k in keys)
                         if e is not None and e.family == gate_family(gate))
        required = shas if required is None else required & shas
    result = resolve(repo, head, keys, family=args.family, required_shas=required)
    print(f"ci-lint attest resolve: {result.key or '(none)'} -- {result.reason}")
    if args.github_output:
        _write_outputs([f"restore_key={result.key or ''}", f"lineage={head.label()}"])
    return 0


def register(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("attest", help="ci-attestations: verify, lineage, cache side entries, resolve (GATE-010, #198)")
    at = p.add_subparsers(dest="attest_command", required=True)

    def common(q: argparse.ArgumentParser) -> None:
        q.add_argument("--repo", default=".")
        q.add_argument("--commit", default="HEAD")

    def lineage_args(q: argparse.ArgumentParser) -> None:
        q.add_argument("--pr", type=int, help="PR number (omit for a default-branch commit)")
        q.add_argument("--main-ref", default="origin/main", help="the default branch ref (full history needed)")

    v = at.add_parser("verify", help="check a commit's Ci-Attestation trailers against ci-attestations.yml")
    common(v)
    v.add_argument("--base", help="read the definition from this commit (a PR's base) instead of the work tree")
    v.set_defaults(func=_cmd_verify)

    lin = at.add_parser("lineage", help="print the commit's ancestor-defining label (m<n>-<sha10> / m<b>-c<k>-<sha10>-pr-<N>)")
    common(lin)
    lineage_args(lin)
    lin.set_defaults(func=_cmd_lineage)

    k = at.add_parser("keys", help="write each valid gate attestation as a tiny side file and print its cache key")
    common(k)
    lineage_args(k)
    k.add_argument("--base", help="read the definition from this commit (a PR's base)")
    k.add_argument("--out-dir", required=True)
    k.add_argument("--slots", type=int, default=8, help="max side entries (one cache-save step per slot)")
    k.add_argument("--github-output", action="store_true",
                   help="write key_<i> (full), stem_<i> (append ${{ env.PR_CACHE_TAG }}), path_<i>, count, lineage")
    k.set_defaults(func=_cmd_keys)

    r = at.add_parser("resolve", help="the nearest ancestor cache entry of a family to hydrate (GET-only REST listing)")
    common(r)
    lineage_args(r)
    r.add_argument("--family", required=True, help="cache key family, e.g. zccache-unit-v1-x86_64-unknown-linux-gnu")
    r.add_argument("--require-gate", action="append", default=[],
                   help="only hydrate from commits whose attestation side entry for this gate exists (repeatable)")
    r.add_argument("--keys-file", help="offline: one cache key per line instead of the live listing")
    r.add_argument("--github-repo", help="owner/name (default: GITHUB_REPOSITORY)")
    r.add_argument("--github-output", action="store_true", help="write restore_key/lineage outputs")
    r.set_defaults(func=_cmd_resolve)
