"""Minimal stdlib GitHub REST (+ GraphQL) client, injectable so tests never
touch the network (round-3A brief, Part 2: "No network in unit tests:
inject a fetch function"; round-4A brief: "GitHub REST via urllib with an
injectable fetch/delete function").

Everything that needs the GitHub API (`ci_lint.reuse`'s title-edit reuse
lookup, `ci_lint.runtime.gate`'s live reuse re-verification, and round-4A's
`ci_lint.cache.audit`/`ci_lint.cache.ops`, which also list, delete and
GraphQL-query caches/PRs) takes a `FetchFn`/`DeleteFn`/`GraphQLFn`, not a
URL and a `urllib` call directly, so a test can pass a fake that reads a
small recorded JSON fixture instead. `default_fetch`/`default_delete`/
`default_graphql` are the real implementations, used only by the CLI layer
(`ci_lint/cli.py`).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable

from ci_lint.cargo_messages import JsonValue

# (url, token) -> parsed JSON body. `token` is always sent as a bearer
# token; every caller here is an authenticated GitHub API read.
FetchFn = Callable[[str, str], JsonValue]

# (url, token) -> None. Raises GitHubApiError on any failure (including a
# non-2xx status). Used by `ci_lint.cache.ops` (heal/trim/janitor); every
# caller MUST have already decided this delete should happen -- this
# function does no policy of its own.
DeleteFn = Callable[[str, str], None]

# (query, token) -> parsed JSON body of the GraphQL response (the whole
# envelope, including a top-level "errors" array if GitHub returned one --
# callers check for it themselves, matching this module's "raise
# GitHubApiError on transport/HTTP failure, let the caller interpret a
# well-formed-but-unhappy body" split for FetchFn/DeleteFn).
GraphQLFn = Callable[[str, str], JsonValue]

_API_ROOT = "https://api.github.com"


class GitHubApiError(Exception):
    """Any network, HTTP, or JSON-decode failure talking to the GitHub API.

    Every caller in this package catches this and degrades to "reuse
    nothing" (or, in round-4A's live cache checks, "needs_review, not a
    silent pass") plus a printed warning (round-3A brief, Part 2: "On any
    API error: reuse nothing and print a warning; never fail the precheck
    because the API is down") -- it must never propagate out of a
    precheck/plan/gate/cache-audit run.
    """


def _headers(token: str, *, accept: str = "application/vnd.github+json") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ci-lint",
    }


def default_fetch(url: str, token: str) -> JsonValue:
    """The real implementation: stdlib `urllib`, no third-party HTTP
    client (AGENTS.md / worker contract: ci_lint is standard-library-only).
    """

    request = urllib.request.Request(url, headers=_headers(token))
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 -- fixed https GitHub API host
            body = response.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise GitHubApiError(f"GET {url} failed: {exc}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise GitHubApiError(f"GET {url}: invalid JSON response: {exc}") from exc


def default_delete(url: str, token: str) -> None:
    """`DELETE` via stdlib `urllib`. Round-4A: `ci_lint.cache.ops` (heal/
    trim/janitor) is the only caller; it requires `actions: write` on the
    token, and every deletion it performs is logged before this is called."""

    request = urllib.request.Request(url, method="DELETE", headers=_headers(token))
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 -- fixed https GitHub API host
            response.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise GitHubApiError(f"DELETE {url} failed: {exc}") from exc


def default_graphql(query: str, token: str) -> JsonValue:
    """POST a GraphQL query via stdlib `urllib`. Round-4A: `ci_lint.cache.
    github_cache.fetch_pr_states` is the only caller -- ONE query batching
    every referenced PR number's state, per the round-4A brief ("closed/
    merged PRs via ONE GraphQL query for all referenced PR numbers")."""

    body = json.dumps({"query": query}).encode("utf-8")
    request = urllib.request.Request(
        f"{_API_ROOT}/graphql",
        data=body,
        method="POST",
        headers={**_headers(token), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 -- fixed https GitHub API host
            resp_body = response.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise GitHubApiError(f"POST /graphql failed: {exc}") from exc
    try:
        return json.loads(resp_body)
    except json.JSONDecodeError as exc:
        raise GitHubApiError(f"POST /graphql: invalid JSON response: {exc}") from exc
