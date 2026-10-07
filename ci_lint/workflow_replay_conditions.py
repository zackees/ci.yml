"""Prove finite source-guard exclusions; never execute workflow expressions."""

import ast
import json
import re
from dataclasses import dataclass

from ci_lint.workflow_replay_inputs import BoundInput
from ci_lint.yaml_io import YamlValue

_TOKEN = re.compile(r"\s+|'(?:[^']|'')*'|[A-Za-z_][A-Za-z0-9_.-]*|&&|\|\||==|!=|[!(),]")


@dataclass(frozen=True)
class GuardValue:
    value: str | bool | None = None
    truth: bool | None = None
    bound: bool = False


@dataclass(frozen=True)
class GuardReference:
    name: str
    reference: str


@dataclass(frozen=True)
class GuardContext:
    inputs: tuple[BoundInput, ...]
    producers: frozenset[str]
    references: tuple[GuardReference, ...]
    successful: bool
    event: str | None


@dataclass(frozen=True)
class ParsedGuard:
    node: ast.Expression
    references: tuple[GuardReference, ...]


def _parse(expression: str) -> ParsedGuard:
    references: list[GuardReference] = []
    parts: list[str] = []
    end = 0
    for match in _TOKEN.finditer(expression):
        if match.start() != end:
            raise ValueError("unsupported condition syntax")
        end = match.end()
        token = match[0]
        if token.isspace():
            continue
        if token.startswith("'"):
            parts.append(json.dumps(token[1:-1].replace("''", "'")))
        elif token == "!":
            # Parse only: Invert has GitHub's high unary precedence, unlike
            # Python's `not`. The AST is interpreted below, never evaluated.
            parts.append("~")
        elif token in ("&&", "||", "true", "false"):
            parts.append({"&&": "and", "||": "or", "true": "True", "false": "False"}[token])
        elif token[0].isalpha() or token[0] == "_":
            name = f"v{len(references)}"
            references.append(GuardReference(name, token))
            parts.append(name)
        else:
            parts.append(token)
    if end != len(expression):
        raise ValueError("unsupported condition syntax")
    parsed = ast.parse(" ".join(parts), mode="eval")
    if sum(1 for _ in ast.walk(parsed)) > 256:
        raise ValueError("condition exceeds bounded syntax limit")
    return ParsedGuard(parsed, tuple(references))


def _name(context: GuardContext, identifier: str) -> str:
    return next((item.reference for item in context.references if item.name == identifier), "")


def _scalar(value: str | bool, *, bound: bool = False) -> GuardValue:
    return GuardValue(value, bool(value), bound)


def _reference(reference: str, context: GuardContext) -> GuardValue:
    if reference == "github.event_name" and context.event is not None:
        return _scalar(context.event, bound=True)
    match = re.fullmatch(r"inputs\.([A-Za-z0-9_-]+)", reference)
    if match:
        found = next((item.value for item in context.inputs if item.name == match[1]), None)
        return _scalar(found, bound=True) if found is not None else GuardValue()
    match = re.fullmatch(r"steps\.([A-Za-z0-9_-]+)\.outcome", reference)
    if match and match[1] in context.producers:
        return _scalar("skipped", bound=True)
    return GuardValue()


def _logical(left: GuardValue, right: GuardValue, *, conjunction: bool) -> GuardValue:
    decisive = False if conjunction else True
    if left.truth is decisive:
        return left
    if left.truth is not None:
        return right
    if right.truth is decisive:
        # Unknown earlier operands may change the scalar result of OR, but
        # cannot change its truth. Preserve that distinction for comparisons.
        return GuardValue(None, decisive, right.bound)
    return GuardValue()


def _compare(node: ast.Compare, context: GuardContext) -> GuardValue:
    if len(node.ops) != 1 or not isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
        return GuardValue()
    left = _value(node.left, context)
    right = _value(node.comparators[0], context)
    if left.value is None or type(left.value) is not type(right.value):
        return GuardValue()
    a, b = left.value, right.value
    if isinstance(a, str) and isinstance(b, str):
        if not a.isascii() or not b.isascii():
            return GuardValue()
        a, b = a.lower(), b.lower()
    equal = a == b
    return _scalar(equal if isinstance(node.ops[0], ast.Eq) else not equal, bound=left.bound or right.bound)


def _call(node: ast.Call, context: GuardContext) -> GuardValue:
    if not isinstance(node.func, ast.Name) or node.keywords:
        return GuardValue()
    name = _name(context, node.func.id)
    if not node.args and name in ("failure", "cancelled", "success", "always"):
        if name == "always":
            return _scalar(True)
        return _scalar(name == "success", bound=True) if context.successful else GuardValue()
    if name == "contains" and len(node.args) == 2:
        values = tuple(_value(arg, context) for arg in node.args)
        if all(isinstance(item.value, str) and item.value.isascii() for item in values):
            return _scalar(str(values[1].value).lower() in str(values[0].value).lower(),
                           bound=any(item.bound for item in values))
    return GuardValue()


def _value(node: ast.AST, context: GuardContext) -> GuardValue:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool)):
        return _scalar(node.value)
    if isinstance(node, ast.Name):
        return _reference(_name(context, node.id), context)
    if isinstance(node, ast.Compare):
        return _compare(node, context)
    if isinstance(node, ast.Call):
        return _call(node, context)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert):
        operand = _value(node.operand, context)
        return (_scalar(not operand.truth, bound=operand.bound)
                if operand.truth is not None else GuardValue())
    if isinstance(node, ast.BoolOp):
        result = _value(node.values[0], context)
        for child in node.values[1:]:
            result = _logical(result, _value(child, context), conjunction=isinstance(node.op, ast.And))
        return result
    return GuardValue()


def condition_excludes(expression: YamlValue, inputs: tuple[BoundInput, ...],
                       producers: set[str], *, successful: bool = False, event: str | None = None) -> bool:
    """A bound source condition must prove false; constants alone never waive.

    `successful` is permitted only for proof requiring success for every
    selected validation. It then excludes failure/cancellation diagnostics.
    Mixed-type comparisons and unrecognized contexts remain unknown.
    """
    if not isinstance(expression, str) or len(expression) > 4096:
        return False
    expression = expression.strip()
    if expression.startswith("${{") and expression.endswith("}}"):
        expression = expression[3:-2].strip()
    try:
        parsed = _parse(expression)
        context = GuardContext(inputs, frozenset(producers), parsed.references, successful, event)
        result = _value(parsed.node.body, context)
    except (SyntaxError, ValueError, RecursionError):
        return False
    return result.truth is False and result.bound
