"""Flow `extends` resolution and the derived-tag registry.

Shared by schema cross-ref validation, the precheck rule groups (TAG-001/002,
CACHE-004) and the planner, so the merge semantics live in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass

from ci_lint.schema import CiToml, Flow, FlowBudget


@dataclass(frozen=True)
class ResolvedFlow:
    id: str
    platforms: tuple[str, ...] | str
    suites: tuple[str, ...] | str
    dylint: str | None
    cache: str | None
    schedule: str | None
    wheels: str | None
    publish: str | None
    janitor: bool
    pre_prune: bool
    budget: FlowBudget | None


def _extends_chain(ci: CiToml, flow_id: str) -> list[Flow]:
    """Base-first chain of flows for `flow_id` (self last). Stops silently on
    a cycle or a dangling `extends`; schema validation (CT-002) already
    reports those as findings -- this function just must not loop forever."""

    chain: list[Flow] = []
    seen: set[str] = set()
    cur: str | None = flow_id
    while cur is not None and cur in ci.flows and cur not in seen:
        seen.add(cur)
        flow = ci.flows[cur]
        chain.append(flow)
        cur = flow.extends
    chain.reverse()
    return chain


def resolve_flow(ci: CiToml, flow_id: str) -> ResolvedFlow:  # noqa: C901
    platforms: tuple[str, ...] | str = ()
    suites: tuple[str, ...] | str = ()
    dylint: str | None = None
    cache: str | None = None
    schedule: str | None = None
    wheels: str | None = None
    publish: str | None = None
    janitor = False
    pre_prune = False
    budget: FlowBudget | None = None

    for flow in _extends_chain(ci, flow_id):
        if flow.platforms is not None:
            platforms = flow.platforms
        if flow.suites is not None:
            suites = flow.suites
        if flow.dylint is not None:
            dylint = flow.dylint
        if flow.cache is not None:
            cache = flow.cache
        if flow.schedule is not None:
            schedule = flow.schedule
        if flow.wheels is not None:
            wheels = flow.wheels
        if flow.publish is not None:
            publish = flow.publish
        if flow.janitor:
            janitor = True
        if flow.pre_prune:
            pre_prune = True
        if flow.budget is not None:
            budget = flow.budget

    return ResolvedFlow(
        id=flow_id,
        platforms=platforms,
        suites=suites,
        dylint=dylint,
        cache=cache,
        schedule=schedule,
        wheels=wheels,
        publish=publish,
        janitor=janitor,
        pre_prune=pre_prune,
        budget=budget,
    )


def platform_groups(ci: CiToml) -> tuple[str, ...]:
    seen: list[str] = []
    for platform in ci.platforms.values():
        if platform.group not in seen:
            seen.append(platform.group)
    return tuple(seen)


def derive_tag_registry(ci: CiToml) -> set[str]:
    """Every tag `ci-lint` recognizes: derived tags plus declared [tags]."""

    tags: set[str] = set()
    for pid in ci.platforms:
        tags.add(f"ci-{pid}")
    for group in platform_groups(ci):
        tags.add(f"ci-{group}")
    for sid in ci.suites:
        tags.add(f"ci-test-{sid}")
        tags.add(f"no-test-{sid}")
    if "perf" in ci.suites:
        for group in platform_groups(ci):
            tags.add(f"ci-perf-{group}")
    tags.update(ci.tags.keys())
    return tags


RESERVED_TAG_PREFIXES: tuple[str, ...] = ("ci-", "no-test", "release")


def is_reserved_token(token: str) -> bool:
    return token.startswith(RESERVED_TAG_PREFIXES)
