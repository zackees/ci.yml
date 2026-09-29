"""`ci-lint cache ...`: the cache runtime (round-4A brief).

- `ci_lint.cache.families` -- the `via` -> key-prefix resolution table.
- `ci_lint.cache.keys` -- `cache key`.
- `ci_lint.cache.save_ok` -- `cache save-ok` (issue #6 §6's do-not-save
  table, CACHE-008).
- `ci_lint.cache.github_cache` -- the injectable REST/GraphQL calls every
  live command below is built on.
- `ci_lint.cache.audit` -- `cache audit` (live, read-only): classification
  and findings (CACHE-001/003/005/006/008/009) + the budget check.
- `ci_lint.cache.ops` -- `cache trim|janitor|heal|preprune` (live,
  read-write; deletes).
- `ci_lint.cache.delta` -- `cache delta manifest|pack|apply` (pure
  filesystem; PR delta packing/unpacking).
"""

from __future__ import annotations
