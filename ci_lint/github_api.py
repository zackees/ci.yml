"""Minimal stdlib GitHub REST client, injectable so tests never touch the
network (round-3A brief, Part 2: "No network in unit tests: inject a fetch
function").

Everything that needs the GitHub Actions API (`ci_lint.reuse`'s title-edit
reuse lookup, `ci_lint.runtime.gate`'s live reuse re-verification) takes a
`FetchFn`, not a URL and a `urllib` call directly, so a test can pass a fake
that reads a small recorded JSON fixture instead. `default_fetch` is the one
real implementation, used only by the CLI layer (`ci_lint/cli.py`).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable

from ci_lint.cargo_messages import JsonValue

# (url, token) -> parsed JSON body. `token` is always sent as a bearer
# token; every caller here is an authenticated GitHub Actions API read.
FetchFn = Callable[[str, str], JsonValue]


class GitHubApiError(Exception):
    """Any network, HTTP, or JSON-decode failure talking to the GitHub API.

    Every caller in this package catches this and degrades to "reuse
    nothing" plus a printed warning (round-3A brief, Part 2: "On any API
    error: reuse nothing and print a warning -- never fail the precheck
    because the API is down") -- it must never propagate out of a
    precheck/plan/gate run.
    """


def default_fetch(url: str, token: str) -> JsonValue:
    """The real implementation: stdlib `urllib`, no third-party HTTP
    client (AGENTS.md / worker contract: ci_lint is standard-library-only).
    """

    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ci-lint",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 -- fixed https GitHub API host
            body = response.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise GitHubApiError(f"GET {url} failed: {exc}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise GitHubApiError(f"GET {url}: invalid JSON response: {exc}") from exc
