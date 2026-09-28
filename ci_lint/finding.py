"""Finding record shared by every rule module.

A finding is deliberately small in round 1: the fields the brief requires
("rule ID, file:line when known, what is wrong, how to fix") plus the status
values the design needs to report approved/expired exceptions. The richer
finding record described in proposal.md section "Machine-checkable
enforcement" (schema/policy versions, selection digest, fingerprints, ...) is
future-round scope for `ci-lint sync-issues`; this module only needs to be
extended, not redesigned, when that lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    VIOLATION = "violation"
    APPROVED_EXCEPTION = "approved_exception"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class Finding:
    """One rule outcome.

    `path` is repo-relative when known, `line` is 1-based when known. `fix`
    must describe a concrete remedy and must never be "add it to the
    allowlist" (AGENTS.md / worker contract requirement).
    """

    rule: str
    message: str
    fix: str
    path: str | None = None
    line: int | None = None
    status: Status = Status.VIOLATION

    def location(self) -> str:
        if self.path is None:
            return "<repo>"
        if self.line is None:
            return self.path
        return f"{self.path}:{self.line}"

    def render(self) -> str:
        tag = {
            Status.VIOLATION: "VIOLATION",
            Status.APPROVED_EXCEPTION: "APPROVED EXCEPTION",
            Status.NEEDS_REVIEW: "NEEDS REVIEW",
        }[self.status]
        return (
            f"[{self.rule}] {tag} {self.location()}\n"
            f"    what:  {self.message}\n"
            f"    fix:   {self.fix}"
        )


@dataclass(frozen=True)
class FindingGroup:
    """A named rule group, cheapest-first, as required by the precheck brief."""

    name: str
    findings: list[Finding] = field(default_factory=list)
