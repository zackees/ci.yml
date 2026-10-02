"""Restricted YAML for hand-edited definition files (zackees/ci.yml#198).

`ci-attestations.yml` is written by people and agents, so it is YAML (comments
allowed), but every consumer -- a developer host with nothing installed, a
CI runner, a future linter -- must parse it identically. This module parses
exactly the subset the policy allows, with the standard library only:

- block mappings (`key: value`, `key:` + an indented block) and block
  sequences (`- item`);
- flow mappings and sequences of scalars (`{lane: rust}`, `[a, b]`);
- scalars: plain, single- or double-quoted, integers, `true`/`false`;
- `#` comments and blank lines.

It **rejects** what would let two parsers disagree or hide content:
anchors (`&`), aliases (`*`), tags (`!`), merge keys (`<<`), block scalars
(`|`, `>`), and more than one document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ci_lint.yaml_io import YamlValue


class MiniYamlError(ValueError):
    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"line {line}: {message}")
        self.line = line


@dataclass(frozen=True)
class _Line:
    number: int
    indent: int
    text: str  # comment stripped, right-trimmed, no indentation


_PLAIN_INT = re.compile(r"^-?\d+$")


def _strip_comment(raw: str, number: int) -> str:
    out: list[str] = []
    quote = ""
    for i, ch in enumerate(raw):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or raw[i - 1] in " \t"):
            break
        out.append(ch)
    if quote:
        raise MiniYamlError(number, "unterminated quoted scalar")
    return "".join(out).rstrip()


def _lines(text: str) -> list[_Line]:
    out: list[_Line] = []
    seen_doc_start = False
    for number, raw in enumerate(text.splitlines(), start=1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise MiniYamlError(number, "tab indentation")
        body = _strip_comment(raw, number)
        if not body.strip():
            continue
        stripped = body.lstrip(" ")
        if stripped in ("---", "..."):
            if seen_doc_start or out or stripped == "...":
                raise MiniYamlError(number, "multiple YAML documents are not allowed")
            seen_doc_start = True
            continue
        out.append(_Line(number, len(body) - len(stripped), stripped))
    return out


def _scalar(token: str, number: int) -> YamlValue:
    token = token.strip()
    if not token:
        return None
    if token[0] in "&*!" or token.startswith("<<") or token[0] in "|>":
        raise MiniYamlError(number, f"'{token[0]}' (anchor, alias, tag, merge key or block scalar) is not allowed")
    if token[0] in ("'", '"'):
        if len(token) < 2 or token[-1] != token[0]:
            raise MiniYamlError(number, f"bad quoted scalar {token!r}")
        inner = token[1:-1]
        return inner.replace("''", "'") if token[0] == "'" else inner.replace('\\"', '"').replace("\\\\", "\\")
    if token in ("true", "false"):
        return token == "true"
    if token in ("null", "~"):
        return None
    if _PLAIN_INT.match(token):
        return int(token)
    if token[0] in "{[":
        return _flow(token, number)
    return token


def _split_flow(body: str, number: int) -> list[str]:
    parts: list[str] = []
    depth = 0
    quote = ""
    current: list[str] = []
    for ch in body:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch in "{[":
            raise MiniYamlError(number, "nested flow collections are not allowed")
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if "".join(current).strip():
        parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _flow(token: str, number: int) -> YamlValue:
    close = "}" if token[0] == "{" else "]"
    if token[-1] != close:
        raise MiniYamlError(number, f"unclosed flow collection {token!r}")
    items = _split_flow(token[1:-1], number)
    if close == "]":
        return [_scalar(item, number) for item in items]
    out: dict[str, YamlValue] = {}
    for item in items:
        key, sep, value = item.partition(":")
        if not sep:
            raise MiniYamlError(number, f"flow mapping item {item!r} has no ':'")
        name = _key(key, number)
        if name in out:
            raise MiniYamlError(number, f"duplicate key {name!r}")
        out[name] = _scalar(value, number)
    return out


def _key(raw: str, number: int) -> str:
    value = _scalar(raw, number)
    if not isinstance(value, str) or not value:
        raise MiniYamlError(number, f"mapping key {raw.strip()!r} must be a non-empty string")
    return value


@dataclass(frozen=True)
class _KeyRest:
    key: str
    rest: str


def _split_key(text: str, number: int) -> _KeyRest | None:
    """`key: rest`; a ':' inside quotes or not followed by
    space/end does not split (so `ci.yml:lint: [..]` keys work)."""

    quote = ""
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == ":" and (i + 1 == len(text) or text[i + 1] == " "):
            return _KeyRest(_key(text[:i], number), text[i + 1 :].strip())
    return None


class _Parser:
    def __init__(self, lines: list[_Line]) -> None:
        self.lines = lines
        self.pos = 0

    def block(self, indent: int) -> YamlValue:
        line = self.lines[self.pos]
        if line.text.startswith("- ") or line.text == "-":
            return self.sequence(indent)
        return self.mapping(indent)

    def mapping(self, indent: int) -> YamlValue:
        out: dict[str, YamlValue] = {}
        while self.pos < len(self.lines) and self.lines[self.pos].indent == indent:
            line = self.lines[self.pos]
            if line.text.startswith("- "):
                raise MiniYamlError(line.number, "sequence item where a mapping key was expected")
            split = _split_key(line.text, line.number)
            if split is None:
                raise MiniYamlError(line.number, f"expected 'key: value', got {line.text!r}")
            key, rest = split.key, split.rest
            if key in out:
                raise MiniYamlError(line.number, f"duplicate key {key!r}")
            self.pos += 1
            if rest:
                out[key] = _scalar(rest, line.number)
            elif self.pos < len(self.lines) and self.lines[self.pos].indent > indent:
                out[key] = self.block(self.lines[self.pos].indent)
            else:
                out[key] = None
        if self.pos < len(self.lines) and self.lines[self.pos].indent > indent:
            raise MiniYamlError(self.lines[self.pos].number, "unexpected indentation")
        return out

    def sequence(self, indent: int) -> YamlValue:
        out: list[YamlValue] = []
        while self.pos < len(self.lines) and self.lines[self.pos].indent == indent:
            line = self.lines[self.pos]
            if not (line.text.startswith("- ") or line.text == "-"):
                break
            self.pos += 1
            item = line.text[1:].strip()
            if _split_key(item, line.number) is not None and not item.startswith(("{", "[", "'", '"')):
                raise MiniYamlError(line.number, "mappings inside block sequences are not supported; use a flow mapping")
            out.append(_scalar(item, line.number))
        return out


def parse(text: str) -> YamlValue:
    lines = _lines(text)
    if not lines:
        return None
    parser = _Parser(lines)
    value = parser.block(lines[0].indent)
    if parser.pos != len(lines):
        raise MiniYamlError(lines[parser.pos].number, "unexpected content (check indentation)")
    return value
