"""Human-readable, ancestor-defining cache keys (zackees/ci.yml#198).

Every cache entry a CI run saves for a commit carries that commit's
*lineage label* in its key:

    m<n>-<sha10>                   a default-branch commit: n = its
                                   first-parent ordinal on main
                                   (`git rev-list --first-parent --count`)
    m<b>-c<k>-<sha10>-pr-<N>       PR #N's commit, k commits (first-parent)
                                   above its merge base with main, whose
                                   own ordinal is b

e.g. `att1-rust.x86_64-unknown-linux-gnu.test-m2189-c1-18204251ce-pr-3532`.
The PR component is a **suffix** on purpose: it is exactly the fleet's
existing `PR_CACHE_TAG` (`-pr-<N>`, empty outside a PR; soldr's
check_pr_cache_keys.py, CACHE-013), so a workflow writes
`<stem>${{ env.PR_CACHE_TAG }}` and one key shape serves main and PRs.

The label is **ancestor-defining**: from the keys alone, without git,
`m<i>` precedes `m<j>` iff i < j (main is never rewritten), and an entry
`m<b>-c<i>-…-pr-N` can precede `m<b>-c<j>-…-pr-N` only when i < j and the
bases match. The 10-hex SHA lets git confirm it, which matters for the
17% of soldr PR pushes that rewrite history (12 rebases, which move `m`,
and 4 amends, which keep it but change the SHA; experiment K1 in
docs/designs/ci-attestations.md). `pr-<N>` is a delimited component, so
the existing PR janitor (CACHE-013) already deletes a closed PR's entries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ci_lint.proc import run_captured

SHA_PREFIX_LEN = 10
_LABEL = re.compile(
    r"(?:^|-)m(?P<m>\d+)(?:-c(?P<c>\d+))?-(?P<sha>[0-9a-f]{10})(?:-pr-(?P<pr>\d+))?$"
)


@dataclass(frozen=True)
class Lineage:
    """Where a commit sits: on main (`pr is None`, `ordinal` unused) or on
    PR #`pr`, `ordinal` first-parent commits above a base whose main
    ordinal is `main_ordinal`."""

    main_ordinal: int
    sha: str
    pr: int | None = None
    ordinal: int = 0

    def stem(self) -> str:
        """The label without its PR tag: what a workflow suffixes with
        `${{ env.PR_CACHE_TAG }}`."""

        short = self.sha[:SHA_PREFIX_LEN]
        if self.pr is None:
            return f"m{self.main_ordinal}-{short}"
        return f"m{self.main_ordinal}-c{self.ordinal}-{short}"

    def label(self) -> str:
        return self.stem() if self.pr is None else f"{self.stem()}-pr-{self.pr}"

    def may_precede(self, later: Lineage) -> bool:
        """Key-only ancestry pre-check (no git): can `self` be an ancestor
        of `later`? Necessary, not sufficient -- confirm with git."""

        if self.pr is None:
            return self.main_ordinal <= later.main_ordinal
        return later.pr == self.pr and later.main_ordinal == self.main_ordinal and self.ordinal <= later.ordinal

    def rank(self) -> tuple[int, ...]:
        """Higher is nearer: a same-PR entry beats any main entry; within a
        kind, the larger ordinal wins."""

        return (1, self.ordinal) if self.pr is not None else (0, self.main_ordinal)


@dataclass(frozen=True)
class KeyedEntry:
    key: str
    family: str  # everything before the lineage label
    lineage: Lineage  # sha holds only the 10-hex prefix until resolved


def parse_key(key: str) -> KeyedEntry | None:
    match = _LABEL.search(key)
    if match is None or (match.group("c") is None) != (match.group("pr") is None):
        return None  # a PR label needs both its ordinal and its tag; main has neither
    family = key[: match.start()].rstrip("-")
    sha = match.group("sha")
    if match.group("pr") is None:
        lineage = Lineage(main_ordinal=int(match.group("m")), sha=sha)
    else:
        lineage = Lineage(main_ordinal=int(match.group("m")), sha=sha, pr=int(match.group("pr")),
                          ordinal=int(match.group("c")))
    return KeyedEntry(key=key, family=family, lineage=lineage)


def cache_key(family: str, lineage: Lineage) -> str:
    return f"{family}-{lineage.label()}"


class LineageError(Exception):
    pass


def _git(repo: Path, *args: str) -> str:
    out = run_captured(["git", "-C", str(repo), *args])
    if not out.ok:
        raise LineageError(f"git {' '.join(args)}: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout.strip()


def lineage_of(repo: Path, commit: str, *, main_ref: str, pr: int | None = None) -> Lineage:
    """Needs full history of `commit` and `main_ref` (fetch-depth: 0)."""

    sha = _git(repo, "rev-parse", f"{commit}^{{commit}}")
    if pr is None:
        return Lineage(main_ordinal=int(_git(repo, "rev-list", "--first-parent", "--count", sha)), sha=sha)
    base = _git(repo, "merge-base", main_ref, sha)
    return Lineage(
        main_ordinal=int(_git(repo, "rev-list", "--first-parent", "--count", base)),
        sha=sha,
        pr=pr,
        ordinal=int(_git(repo, "rev-list", "--first-parent", "--count", f"{base}..{sha}")),
    )


@dataclass(frozen=True)
class Resolution:
    key: str | None  # the exact key to restore, or None (cold)
    considered: int
    reason: str


def resolve(repo: Path, head: Lineage, keys: list[str], *, family: str,
            required_shas: frozenset[str] | None = None) -> Resolution:
    """The nearest ancestor of `head` among `keys` of `family`. Pre-filter
    by key (ancestor-defining labels), then confirm each candidate with
    `git merge-base --is-ancestor`. `required_shas` (10-hex prefixes)
    restricts the choice to commits with some other evidence -- e.g. an
    attestation side entry or a green run -- so a cache is hydrated only
    from a state known to be good."""

    entries = [e for e in (parse_key(k) for k in keys) if e is not None and e.family == family]
    candidates = [e for e in entries if e.lineage.may_precede(head)]
    if required_shas is not None:
        candidates = [e for e in candidates if e.lineage.sha in required_shas]
    for entry in sorted(candidates, key=lambda e: e.lineage.rank(), reverse=True):
        try:
            full = _git(repo, "rev-parse", "--verify", "--quiet", f"{entry.lineage.sha}^{{commit}}")
        except LineageError:
            continue
        if run_captured(["git", "-C", str(repo), "merge-base", "--is-ancestor", full, head.sha]).ok:
            return Resolution(entry.key, len(entries), f"nearest ancestor {entry.lineage.label()}")
    return Resolution(None, len(entries), f"no ancestor entry among {len(entries)} '{family}' key(s)")
