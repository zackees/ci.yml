"""Cache family resolution: `[cache.family.<id>].via` -> the real GitHub
Actions cache-key prefix/shape it produces (round-4A brief, deliverable 1).

Every `setup-soldr:*` prefix here is cited against a line in setup-soldr's
own source, read from the reference clone the round-4A brief pointed at
(setup-soldr is READ-ONLY for this worker -- see the worker contract's
"Repositories" section), verified 2026-09-28 on its default branch. A
`via = "ci-lint"` family is this package's own convention, not
setup-soldr's, so it needs no such citation -- see `resolve_prefix` below.

Re-verify this table (and bump the fixtures under
ci_lint/tests/fixtures/runtime/cache/) whenever setup-soldr bumps a cache
schema version (the "-vN-" segment in a prefix).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FamilyShape:
    via: str
    prefix: str
    source: str
    note: str = ""


SETUP_SOLDR_FAMILY_SHAPES: tuple[FamilyShape, ...] = (
    FamilyShape(
        via="setup-soldr:build-cache",
        prefix="setup-soldr-buildcache-v2-",
        source="setup-soldr src/lib/resolve-setup.ts:829 -- "
        "buildCachePrefix = `setup-soldr-buildcache-v2-${runnerOs}-${runnerArch}`",
    ),
    FamilyShape(
        via="setup-soldr:cargo-registry",
        prefix="setup-soldr-cargoregistry-v1-",
        source="setup-soldr src/lib/resolve-setup.ts:1055 -- cargoRegistryCachePrefix",
        note="v1 unless cargoRegistryArchiveFormat() resolves to 'soldr-v2' (then v2 instead); v1 "
        "is the shape observed live on template-python-rust-cmd's main on 2026-09-28.",
    ),
    FamilyShape(
        via="setup-soldr:cook",
        prefix="cook-base-v2-",
        source="setup-soldr src/lib/cook-cache.ts:247 -- COOK_BASE_KEY_PREFIX = \"cook-base-v2\"",
    ),
    FamilyShape(
        via="setup-soldr:cook-delta",
        prefix="cook-delta-v2-",
        source="setup-soldr src/lib/cook-cache.ts:248 -- COOK_DELTA_KEY_PREFIX = \"cook-delta-v2\"",
        note="retired fleet-wide (setup-soldr#533, issue #6 D14); declared here only so a "
        "[cache].retired entry naming it resolves to a real prefix for the live audit.",
    ),
    FamilyShape(
        via="setup-soldr:cross-targets",
        prefix="setup-soldr-prepare-v3-",
        source="setup-soldr src/lib/blessed-cross-prepare.ts:89",
    ),
    FamilyShape(
        via="setup-soldr:dylint",
        prefix="setup-soldr-dylint-v2-",
        source="setup-soldr src/lib/resolve-setup.ts:1185-1186 -- "
        "dylintCacheSchema = dylintModeEnabled ? \"v2\" : \"v1\"; dylintCacheKey",
        note="one cache entry covers the dylint tool/driver/foundation together (a single hash "
        "over all three -- resolve-setup.ts:~1155-1183); v1 when dylintModeEnabled is false.",
    ),
    FamilyShape(
        via="setup-soldr:dylint-output",
        prefix="setup-soldr-dylint-output-v1-",
        source="setup-soldr src/lib/resolve-setup.ts:1204 -- dylintOutputKey",
    ),
    FamilyShape(
        via="setup-soldr:soldr-mini",
        prefix="soldr-mini-v2-",
        source="setup-soldr src/lib/soldr-mini-cache.ts:85 -- MINI_KEY_PREFIX = \"soldr-mini-v2\"",
    ),
    FamilyShape(
        via="setup-soldr:solo-toolchain",
        prefix="solo-toolchain-v3-",
        source="setup-soldr src/lib/solo-toolchain-cache.ts:276,280 -- "
        "SOLO_CACHE_SCHEMA_VERSION = 3; base = `solo-toolchain-v${SOLO_CACHE_SCHEMA_VERSION}-...`",
        note="retired in the template (issue #6 D14); declared here only so a [cache].retired "
        "entry naming it resolves to a real prefix for the live audit.",
    ),
)

SETUP_SOLDR_SHAPES_BY_VIA: dict[str, FamilyShape] = {s.via: s for s in SETUP_SOLDR_FAMILY_SHAPES}

# `via = "ci-lint"`: a ci-lint-owned family. Its prefix is the family's OWN
# declared id (the `[cache.family.<id>]` table key), never a fixed string
# -- e.g. the canonical example's `uv` family -> `uv-v1-`. This is
# ci-lint's own convention (issue #6 §6: "ci-lint cache key <family> builds
# the rest ... from the components declared in ci.toml"), not a
# setup-soldr shape, so it has no source citation into that repository.
# The round-4A brief's family table writes this generically as
# "ci-lint:<family> -> <family>-v1-"; `via = "ci-lint"` (no colon) is the
# literal schema-3 value the canonical example already uses (docs/ci-toml.md
# documented it as such since round-1A) -- this module treats them as the
# same convention rather than introducing a second `via` spelling.
CI_LINT_VIA = "ci-lint"
CI_LINT_KEY_VERSION = "v1"

# PR delta keys are a distinct shape layered on top of any base family's
# own identity (issue #6 §6): `delta-v1-pr<N>-<family>-<platform>-
# b<first-8-hex-of-sha256(base key)>`. This is ci-lint's own convention,
# built by `ci_lint.cache.keys.build_delta_key`.
DELTA_KEY_VERSION = "v1"
DELTA_PREFIX = f"delta-{DELTA_KEY_VERSION}-pr"

# Every `via` value schema-3 accepts. `ci_lint.schema` cross-checks
# `[cache.family.<id>].via` against this at load time (CT-002 on anything
# else) -- round-4A brief, deliverable 1: "Add setup-soldr:soldr-mini and
# setup-soldr:dylint to the schema's allowed via values."
ALLOWED_VIA_VALUES: frozenset[str] = frozenset({CI_LINT_VIA, *SETUP_SOLDR_SHAPES_BY_VIA})


def resolve_prefix(via: str, family_id: str) -> str | None:
    """The literal cache-key prefix `via` (a `[cache.family.<id>].via`
    value) produces. `None` for an unrecognized `via` -- `ci_lint.schema`
    should already have rejected it as CT-002; this function just must
    never fabricate a prefix for something it doesn't know."""

    if via == CI_LINT_VIA:
        return f"{family_id}-{CI_LINT_KEY_VERSION}-"
    shape = SETUP_SOLDR_SHAPES_BY_VIA.get(via)
    return shape.prefix if shape is not None else None


def resolve_retired_prefix(retired_name: str) -> str:
    """`[cache].retired` entries are bare family/prefix names (e.g.
    `"solo-toolchain"`, `"cook-delta-v2"`), not `via` values -- a live
    cache key matches one when it starts with `"<name>-"`. Deliberately a
    generic prefix match rather than a `via` lookup, so a retired entry
    that predates this table, or that the fleet renamed, still matches
    something concrete; see `ci_lint.cache.audit`'s CACHE-009."""

    return retired_name if retired_name.endswith("-") else f"{retired_name}-"
