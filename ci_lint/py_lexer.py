"""Blank Python comments and string literals using stdlib `tokenize`.

Round-2A brief, defect 2: `ci_lint.rules.layout`'s Python LAYOUT-001 scan
matched selector substrings (`sys.platform`, `os.name`, ...) against raw
source lines, so a comment like `# sys.platform check lives in platforms/`
was flagged exactly like real code. `tokenize` (stdlib, matching AGENTS.md's
standard-library-only rule) is the correct tool: it already knows exactly
which spans of the source are `COMMENT` or `STRING` tokens, including
multi-line triple-quoted strings and f-strings, without re-implementing a
Python lexer by hand.

`strip_comments_and_strings` blanks those spans' *content* (leaving
newlines alone, so line numbers of the surviving code are unchanged) and
returns the raw source unmodified if the file does not even tokenize (a
syntax error is a different problem; the caller's own file read already
tolerates that by falling back to an empty scan elsewhere, so returning the
original text here just means "scan everything", never "skip it").
"""

from __future__ import annotations

import io
import tokenize

_STRING_LIKE_TOKEN_NAMES: tuple[str, ...] = (
    "STRING",
    "FSTRING_START",
    "FSTRING_MIDDLE",
    "FSTRING_END",
)


def _string_like_and_comment_token_types() -> frozenset[int]:
    names = ("COMMENT", *_STRING_LIKE_TOKEN_NAMES)
    return frozenset(getattr(tokenize, name) for name in names if hasattr(tokenize, name))


_BLANK_TYPES = _string_like_and_comment_token_types()


def strip_comments_and_strings(source: str) -> str:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return source

    lines = source.splitlines(keepends=True)
    line_starts = [0]
    for line in lines:
        line_starts.append(line_starts[-1] + len(line))

    def offset(row: int, col: int) -> int:
        if row < 1 or row > len(lines):
            return len(source)
        return line_starts[row - 1] + col

    chars = list(source)
    for tok in tokens:
        if tok.type not in _BLANK_TYPES:
            continue
        start = offset(*tok.start)
        end = offset(*tok.end)
        for idx in range(start, min(end, len(chars))):
            if chars[idx] != "\n":
                chars[idx] = " "
    return "".join(chars)
