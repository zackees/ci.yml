"""A tiny glob-to-regex matcher for repo-relative paths.

`pathlib.PurePath.full_match` (recursive `**` support) only exists from
Python 3.13; ci_lint targets Python >=3.11, so this implements the small
subset of glob syntax `ci.toml`'s allowlists actually use (`*`, `**`, `?`)
by hand instead of depending on it.
"""

from __future__ import annotations

import re


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                if i + 2 < n and pattern[i + 2] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
                continue
            out.append("[^/]*")
            i += 1
            continue
        if c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def matches_any(rel: str, patterns: tuple[str, ...]) -> bool:
    return any(glob_to_regex(p).match(rel) for p in patterns)
