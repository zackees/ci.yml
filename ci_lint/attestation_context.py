"""Trusted event parsing for a one-line GitHub attestation publisher."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ci_lint.cargo_messages import JsonValue
from ci_lint.proc import run_captured


@dataclass(frozen=True)
class PublicationContext:
    enabled: bool
    commit: str = "HEAD"
    base: str | None = None
    pr: int | None = None
    main_ref: str = "origin/main"
    promote: bool = False


def _sha(value: JsonValue) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
        raise ValueError("publisher commit SHA must be 40 or 64 lowercase hexadecimal characters")
    return value


def _document(env: Mapping[str, str]) -> dict[str, JsonValue]:
    path = env.get("GITHUB_EVENT_PATH")
    if not path:
        raise ValueError("--github-context requires GITHUB_EVENT_PATH")
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("cannot read GitHub event JSON") from exc
    if not isinstance(doc, dict):
        raise ValueError("GitHub event JSON is not an object")
    return doc


def publication_context(env: Mapping[str, str]) -> PublicationContext:
    event = env.get("GITHUB_EVENT_NAME")
    if event not in ("pull_request", "push"):
        return PublicationContext(False)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", env.get("GITHUB_REPOSITORY", "")):
        raise ValueError("--github-context requires a valid GITHUB_REPOSITORY")
    doc = _document(env)
    repository = doc.get("repository")
    branch = repository.get("default_branch") if isinstance(repository, dict) else None
    if not isinstance(branch, str) or not run_captured(["git", "check-ref-format", f"refs/heads/{branch}"]).ok:
        raise ValueError("GitHub event lacks a safe default branch")
    if event == "push":
        if env.get("GITHUB_REF") != f"refs/heads/{branch}":
            return PublicationContext(False)
        return PublicationContext(True, _sha(env.get("GITHUB_SHA")), main_ref=f"origin/{branch}", promote=True)
    return _pr_context(doc, env, branch)


def _pr_context(doc: dict[str, JsonValue], env: Mapping[str, str], branch: str) -> PublicationContext:
    pr = doc.get("pull_request")
    number = doc.get("number")
    if not isinstance(pr, dict) or not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        raise ValueError("GitHub event lacks a PR with a positive integer number")
    head, base = pr.get("head"), pr.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        raise ValueError("GitHub PR event lacks head/base objects")
    head_repo = head.get("repo")
    if not isinstance(head_repo, dict) or head_repo.get("full_name") != env.get("GITHUB_REPOSITORY"):
        raise ValueError("attestation publisher only accepts same-repository PRs")
    return PublicationContext(True, _sha(head.get("sha")), _sha(base.get("sha")), number, f"origin/{branch}")
