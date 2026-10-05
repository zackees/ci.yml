"""The delete plan must be ordered so a bounded run reclaims the most it can.

zackees/ci.yml#357: `max_deletes` truncated the plan in dict-insertion order.
On zackees/bosn that put the entire default cap (50) on 440-byte attestation
entries while 15 superseded multi-hundred-MB `compile` entries (9.44 GiB) sat
outside it -- so `cache janitor` reclaimed 44 KB of 9.44 GiB available. The
SELECTION was always correct; only the ordering before truncation was wrong.
"""

from __future__ import annotations

import unittest

from ci_lint.cache.ops import DeletePlanEntry, _plan_rank, _prioritise_plan


def _entry(cache_id: int, size: int, reason: str) -> DeletePlanEntry:
    return DeletePlanEntry(id=cache_id, key=f"k{cache_id}", size_in_bytes=size, reason=reason)


class PrioritisePlanTest(unittest.TestCase):
    def test_superseded_entries_come_before_small_undeclared_ones(self) -> None:
        plan = {
            1: _entry(1, 440, "CACHE-001: cache undeclared family: id=1"),
            2: _entry(2, 440, "CACHE-001: cache undeclared family: id=2"),
            3: _entry(3, 900_000_000, "CACHE-006: cache id=3 is a superseded 'compile' entry"),
        }
        ordered = _prioritise_plan(plan)
        self.assertEqual([e.id for e in ordered][0], 3, "the 900 MB superseded entry must sort first")

    def test_largest_first_within_a_class(self) -> None:
        plan = {
            1: _entry(1, 100, "CACHE-008: merged PR"),
            2: _entry(2, 900, "CACHE-008: merged PR"),
            3: _entry(3, 400, "CACHE-008: merged PR"),
        }
        self.assertEqual([e.id for e in _prioritise_plan(plan)], [2, 3, 1])

    def test_truncation_then_keeps_the_big_ones(self) -> None:
        """The regression itself: a cap of 1 over a bosn-shaped plan must pick
        a superseded entry, not an attestation entry."""

        plan = {i: _entry(i, 440, "CACHE-001: undeclared") for i in range(1, 60)}
        plan[999] = _entry(999, 1_000_000_000, "CACHE-006: superseded")
        ordered = _prioritise_plan(plan)
        self.assertEqual(ordered[0].id, 999)
        self.assertEqual([e.id for e in ordered[:1]], [999])

    def test_rank_ordering(self) -> None:
        self.assertLess(_plan_rank("CACHE-006: ..."), _plan_rank("CACHE-001: ..."))
        self.assertLess(_plan_rank("CACHE-008: ..."), _plan_rank("stale: not accessed"))
        # LRU/stale prose reasons and any unrecognised future reason share the
        # lowest rank -- there is no evidence-based reason to prefer one, and
        # inventing a distinction would be arbitrary.
        self.assertEqual(_plan_rank("lru: ..."), _plan_rank("some future reason"))
        self.assertGreater(_plan_rank("lru: ..."), _plan_rank("CACHE-005: ..."))

    def test_order_is_deterministic_for_equal_sizes(self) -> None:
        plan = {
            5: _entry(5, 100, "CACHE-008: merged PR"),
            2: _entry(2, 100, "CACHE-008: merged PR"),
        }
        self.assertEqual([e.id for e in _prioritise_plan(plan)], [2, 5])
