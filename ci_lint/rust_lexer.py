"""A small Rust lexer that blanks comments and string-literal content.

Round-2A brief, defect 2: `ci_lint.rules.layout`'s LAYOUT-001 scan and
`ci_lint.cargo_scan.has_cfg_feature` (RUST-011) matched on raw source
lines, so a doc comment merely *mentioning* a selector -- for example
`//! supports #[cfg(target_os = "linux")] per platform` or
`/// see cfg(feature = "json")` -- was flagged exactly like real code.

`strip_comments_and_strings` is not a full Rust tokenizer: it only needs to
turn the *content* of `//`, `//!`, `///` and `/* */` (nestable) comments,
plus `"..."`, `b"..."`, raw (`r"..."`, `r#"...Byte"#`, ...) and raw-byte
string literals, into blanks -- preserving every newline so line numbers of
the surviving code are unchanged, and leaving single-quoted lifetimes
(`'a`, `'static`) alone rather than misreading them as unterminated char
literals.
"""

from __future__ import annotations


def _is_blank(ch: str) -> str:
    return ch if ch == "\n" else " "


def _char_literal_len(source: str, i: int) -> int | None:
    """If `source[i] == \"'\"` starts a char literal (`'x'`, `'\\n'`,
    `'\\u{1F600}'`, ...), return its length including both quotes; else
    None (it is a lifetime like `'a` or `'static`, or a lone `'`)."""

    n = len(source)
    j = i + 1
    if j >= n:
        return None
    if source[j] == "\\":
        k = j + 1
        if k >= n:
            return None
        if source[k] == "u" and k + 1 < n and source[k + 1] == "{":
            end_brace = source.find("}", k)
            if end_brace == -1:
                return None
            k = end_brace + 1
        else:
            k += 1
        if k < n and source[k] == "'":
            return k + 1 - i
        return None
    if source[j] != "'" and j + 1 < n and source[j + 1] == "'":
        return j + 2 - i
    return None


def _raw_string_prefix_len(source: str, i: int) -> tuple[int, int] | None:
    """If a raw (byte) string literal starts at `i` (`r"`, `r#"`, `br##"`,
    ...), return `(prefix_len, hash_count)` where `prefix_len` covers
    everything up to and including the opening quote; else None."""

    n = len(source)
    j = i
    if j < n and source[j] == "b":
        j += 1
    if j >= n or source[j] != "r":
        return None
    j += 1
    hashes = 0
    while j < n and source[j] == "#":
        hashes += 1
        j += 1
    if j >= n or source[j] != '"':
        return None
    return (j + 1 - i, hashes)


def strip_comments_and_strings(source: str) -> str:
    out: list[str] = []
    i = 0
    n = len(source)

    while i < n:
        ch = source[i]

        # `//`, `//!`, `///` line comments: blank to end of line.
        if ch == "/" and i + 1 < n and source[i + 1] == "/":
            while i < n and source[i] != "\n":
                out.append(" ")
                i += 1
            continue

        # `/* ... */` block comments, nestable.
        if ch == "/" and i + 1 < n and source[i + 1] == "*":
            depth = 1
            out.append("  ")
            i += 2
            while i < n and depth > 0:
                if source[i : i + 2] == "/*":
                    depth += 1
                    out.append("  ")
                    i += 2
                elif source[i : i + 2] == "*/":
                    depth -= 1
                    out.append("  ")
                    i += 2
                else:
                    out.append(_is_blank(source[i]))
                    i += 1
            continue

        # raw / raw-byte strings: (b)?r(#*)"..."(#*)
        raw = _raw_string_prefix_len(source, i)
        if raw is not None:
            prefix_len, hashes = raw
            out.append(" " * prefix_len)
            body_start = i + prefix_len
            closer = '"' + "#" * hashes
            end = source.find(closer, body_start)
            if end == -1:
                out.append("".join(_is_blank(c) for c in source[body_start:n]))
                i = n
            else:
                out.append("".join(_is_blank(c) for c in source[body_start:end]))
                out.append(" " * len(closer))
                i = end + len(closer)
            continue

        # normal / byte strings: "..." or b"..." (with \-escapes).
        if ch == '"' or (ch == "b" and i + 1 < n and source[i + 1] == '"'):
            start = i
            i += 1 if ch == '"' else 2
            out.append(" " * (i - start))
            while i < n and source[i] != '"':
                if source[i] == "\\" and i + 1 < n:
                    out.append(_is_blank(source[i]))
                    out.append(_is_blank(source[i + 1]))
                    i += 2
                    continue
                out.append(_is_blank(source[i]))
                i += 1
            if i < n:
                out.append(" ")
                i += 1
            continue

        # char literal vs. lifetime.
        if ch == "'":
            length = _char_literal_len(source, i)
            if length is not None:
                out.append(" " * length)
                i += length
                continue
            out.append(ch)
            i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)
