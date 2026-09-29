"""`ci-lint cache key <family>`: build a cache key for a ci-lint-owned
family, and the PR-delta key wrapper for any family (round-4A brief,
deliverable 2).

setup-soldr owns its own families' base keys (registry, deps/cook,
compile/build-cache, sdk/cross-targets, dylint, dylint-out, soldr-mini,
solo-toolchain) -- it computes them from toolchain, lockfile and target
state this package cannot reproduce without invoking soldr itself (issue #6
§6: "Keys are built by exactly two builders, never written by hand").  This
module builds the OTHER builder's half: a `via = "ci-lint"` family's own
key from its declared `[cache.family.<id>].key` components, and the delta
wrapper `delta-v1-pr-<N>-<family>-<platform>-b<base8>`, which wraps ANY
family's base key -- a setup-soldr one included, passed in literally via
`--base-key` since only setup-soldr (or a restore step that already has it)
knows that string at runtime.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ci_lint.cache.families import CI_LINT_VIA, DELTA_KEY_VERSION, resolve_prefix
from ci_lint.schema import CiToml

# Components a [cache.family.<id>].key entry may name besides a literal
# repo-relative file path to hash (e.g. "uv.lock", "Cargo.lock").
_OS_COMPONENT = "os"
_PYTHON_COMPONENT = "python"


class CacheKeyError(ValueError):
    """Bad input building a cache key -- the CLI layer turns this into
    exit 2 (round-4A brief: "exit 2 on bad input")."""


def _hash_file(path: Path) -> str:
    if not path.is_file():
        raise CacheKeyError(f"key component file not found: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest[:16]


def _resolve_component(component: str, *, ci: CiToml, platform_id: str | None, repo_root: Path) -> str:
    if component == _OS_COMPONENT:
        if platform_id is None:
            raise CacheKeyError("key component 'os' needs --platform")
        platform = ci.platforms.get(platform_id)
        if platform is None:
            raise CacheKeyError(f"--platform {platform_id!r} is not declared in [platforms]")
        return platform.id
    if component == _PYTHON_COMPONENT:
        if ci.python is None or not ci.python.pythons:
            raise CacheKeyError("key component 'python' needs [python].pythons in ci.toml")
        return ci.python.pythons[0]  # the floor version -- PR smoke uses it too (docs/ci-toml.md)
    # Anything else is a repo-relative lockfile/manifest to hash (e.g. "uv.lock").
    return _hash_file(repo_root / component)


@dataclass(frozen=True)
class FamilyKey:
    family: str
    prefix: str
    components: tuple[str, ...]

    @property
    def key(self) -> str:
        if not self.components:
            return self.prefix.rstrip("-")
        return self.prefix + "-".join(self.components)


def build_family_key(ci: CiToml, family_id: str, *, platform_id: str | None, repo_root: Path) -> FamilyKey:
    """Build the base key for a declared `[cache.family.<id>]`. Only
    meaningful for `via = "ci-lint"` families with their own `key`
    components -- an external family's (`setup-soldr:*`, `setup-uv`) TRUE
    base key can only come from that action itself (its prefix is still
    resolvable here, e.g. for `ci-lint cache heal`'s exact-key deletes, but
    this function cannot reproduce its real hash suffix, and does not try
    to). A `.key` array declared on a non-`"ci-lint"` family is therefore
    ignored here -- appending ci-lint-resolved components (os/python/a
    lockfile hash, in ci-lint's OWN ordering) onto an external prefix would
    silently fabricate a key shape that action never actually produces."""

    fam = ci.cache.family.get(family_id)
    if fam is None:
        raise CacheKeyError(f"'{family_id}' is not declared in [cache.family]")
    prefix = resolve_prefix(fam.via, family_id)
    if prefix is None:
        raise CacheKeyError(
            f"[cache.family.{family_id}].via = {fam.via!r} is not a recognized via value"
        )
    if fam.via != CI_LINT_VIA:
        return FamilyKey(family=family_id, prefix=prefix, components=())
    components = tuple(
        _resolve_component(c, ci=ci, platform_id=platform_id, repo_root=repo_root) for c in (fam.key or ())
    )
    return FamilyKey(family=family_id, prefix=prefix, components=components)


def build_delta_key(*, family_id: str, platform_id: str, pr: int, base_key: str) -> str:
    """`delta-v1-pr-<N>-<family>-<platform>-b<first 8 hex of
    sha256(base key)>` (issue #6 §6, "PR caches: a small delta, never a
    base"). `base_key` is the literal restored base cache's key string --
    the self-heal property ("A delta whose b<hash> doesn't match the
    restored base is discarded") falls directly out of hashing that exact
    string, not some derived identity of it."""

    base8 = hashlib.sha256(base_key.encode("utf-8")).hexdigest()[:8]
    return f"delta-{DELTA_KEY_VERSION}-pr-{pr}-{family_id}-{platform_id}-b{base8}"
