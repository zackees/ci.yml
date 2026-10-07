"""Rebind trusted merged-PR evidence for main-scoped cache publication.

This never skips main jobs or edits commits. Release events cannot promote.
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from ci_lint.cargo_messages import JsonValue
from ci_lint.attestations import CommitAttestations, Definition, GateAttestation, VALID, load_definition, parse_trailers, verify_commit
from ci_lint.gate_trust import TRUSTED_ASSOCIATIONS, TrustInput, decide as decide_trust
from ci_lint.github_api import FetchFn, GitHubApiError
from ci_lint.freshness import fresh
from ci_lint.proc import run_captured


@dataclass(frozen=True)
class PromotionInput:
    slug: str
    commit: str
    event: str
    ref: str
    default_branch: str
    now: int
    max_age_seconds: int = 86400
    fetch_missing: bool = False


@dataclass(frozen=True)
class Promotion:
    records: tuple[GateAttestation, ...]
    reason: str
    source: str = ""
    pr: int | None = None


def _git(repo: Path, *args: str) -> str:
    result = run_captured(["git", "-C", str(repo), *args])
    return result.stdout.strip() if result.ok else ""


def promote(repo: Path, inp: PromotionInput, fetch: FetchFn, token: str) -> Promotion:
    if inp.event != "push" or inp.ref != f"refs/heads/{inp.default_branch}":
        return Promotion((), "not-default-push")
    if not token or not inp.slug or inp.max_age_seconds <= 0:
        return Promotion((), "missing-input")
    try:
        pulls = fetch(f"https://api.github.com/repos/{inp.slug}/commits/{inp.commit}/pulls?per_page=100", token)
    except GitHubApiError:
        return Promotion((), "api-error")
    if not isinstance(pulls, list) or len(pulls) >= 100:
        return Promotion((), "ambiguous-pr")
    matches = [p for p in pulls if isinstance(p, dict) and p.get("merged_at")
               and p.get("merge_commit_sha") == inp.commit]
    if len(matches) != 1:
        return Promotion((), "ambiguous-pr")
    pr = matches[0]
    if inp.fetch_missing and not _ensure_source(repo, inp, pr, token):
        return Promotion((), "source-unavailable")
    return _from_pr(repo, inp, pr)


def _from_pr(repo: Path, inp: PromotionInput, pr: dict[str, JsonValue]) -> Promotion:
    base, head = pr.get("base"), pr.get("head")
    if not isinstance(base, dict) or not isinstance(head, dict) or base.get("ref") != inp.default_branch:
        return Promotion((), "wrong-base")
    base_repo, head_repo = base.get("repo"), head.get("repo")
    if not isinstance(base_repo, dict) or not isinstance(head_repo, dict) or base_repo.get("full_name") != inp.slug \
            or head_repo.get("full_name") != inp.slug:
        return Promotion((), "fork")
    base_sha, source = base.get("sha"), head.get("sha")
    if not isinstance(base_sha, str) or not isinstance(source, str):
        return Promotion((), "missing-sha")
    labels = pr.get("labels")
    if not isinstance(labels, list):
        return Promotion((), "malformed-labels")
    association = pr.get("author_association")
    if not isinstance(association, str) or not _valid_labels(labels):
        return Promotion((), "malformed-pr")
    names = tuple(p["name"] for p in labels if isinstance(p, dict) and isinstance(p.get("name"), str))
    trust = decide_trust(repo, TrustInput("pull_request", source, base_sha, association,
                                        inp.slug, inp.slug, names))
    if not trust.trusted:
        return Promotion((), f"untrusted:{trust.reason}")
    number = pr.get("number")
    return _promote_source(repo, inp, base_sha, source, number if isinstance(number, int) else None)


def _promote_source(repo: Path, inp: PromotionInput, base_sha: str, source: str, number: int | None) -> Promotion:
    definition = _unchanged_definition(repo, base_sha, inp.commit)
    if definition is None:
        return Promotion((), "definition-changed")
    tree = _git(repo, "rev-parse", f"{inp.commit}^{{tree}}")
    if not tree or tree != _git(repo, "rev-parse", f"{source}^{{tree}}"):
        return Promotion((), "tree-mismatch")
    verified = verify_commit(repo, source, definition)
    if verified.errors or verified.malformed:
        return Promotion((), "invalid-source")
    parents = tuple(_git(repo, "log", "-1", "--format=%P", inp.commit).split())
    if not parents:
        return Promotion((), "missing-main-parents")
    valid = _rebound_records(repo, source, tree, parents, verified, inp)
    return Promotion(valid, "promoted" if valid else "no-fresh-evidence", source, number)


def _rebound_records(repo: Path, source: str, tree: str, parents: tuple[str, ...],
                     verified: CommitAttestations, inp: PromotionInput) -> tuple[GateAttestation, ...]:
    records = tuple(p.attestation for p in parse_trailers(_git(repo, "log", "-1", "--format=%B", source))
                    if p.attestation is not None)
    return tuple(replace(a, tree=tree, parents=parents).stamped() for a in records
                 if verified.state(a.gate) == VALID
                 and fresh(a.at, inp.now, inp.max_age_seconds, clock_skew_seconds=0))


def _unchanged_definition(repo: Path, base: str, commit: str) -> Definition | None:
    original = load_definition(repo, rev=base)
    current = load_definition(repo, rev=commit)
    if original is None or current is None or original.findings or current.findings \
            or original.definition is None or original.definition != current.definition:
        return None
    return original.definition


def _valid_labels(labels: list[JsonValue]) -> bool:
    return all(isinstance(p, dict) and isinstance(p.get("name"), str) for p in labels)


def _ensure_source(repo: Path, inp: PromotionInput, pr: dict[str, JsonValue], token: str) -> bool:
    head, base = pr.get("head"), pr.get("base")
    association = pr.get("author_association")
    if not isinstance(association, str) or association not in TRUSTED_ASSOCIATIONS:
        return False
    number = pr.get("number")
    if not isinstance(head, dict) or not isinstance(base, dict) or not isinstance(number, int) \
            or isinstance(number, bool) or number <= 0:
        return False
    head_repo = head.get("repo")
    source = head.get("sha")
    base_sha = base.get("sha")
    if not isinstance(base_sha, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", base_sha):
        return False
    if not isinstance(head_repo, dict) or head_repo.get("full_name") != inp.slug \
            or base.get("ref") != inp.default_branch or not isinstance(source, str) \
            or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", source) \
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", inp.slug):
        return False
    if _git(repo, "rev-parse", "--verify", f"{source}^{{commit}}") == source:
        return True
    env = os.environ.copy()
    env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode(),
                "GIT_TERMINAL_PROMPT": "0"})
    try:
        result = run_captured(["git", "-C", str(repo), "fetch", "--no-tags", "--no-write-fetch-head",
                               f"https://github.com/{inp.slug}.git", f"refs/pull/{number}/head:refs/ci-lint/promotion/{number}"], env=env, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.ok and _git(repo, "rev-parse", "--verify", f"refs/ci-lint/promotion/{number}^{{commit}}") == source
