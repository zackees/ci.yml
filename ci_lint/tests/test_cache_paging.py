"""CACHE-033 -- a read of the Actions cache must paginate."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ci_lint.rules.cache_paging import check_cache_033


class CachePagingTest(unittest.TestCase):
    def _scan(self, script: str) -> list[str]:
        d = Path(tempfile.mkdtemp())
        (d / "ci").mkdir()
        (d / "ci" / "probe.py").write_text(script, encoding="utf-8")
        return [f.message for f in check_cache_033(d)]

    def test_a_bare_single_page_read_is_a_finding(self) -> None:
        found = self._scan(
            "import requests\n"
            "r = requests.get(f'https://api.github.com/repos/{repo}/actions/caches')\n"
        )
        self.assertEqual(len(found), 1)
        self.assertIn("without pagination", found[0])

    def test_a_page_walk_is_accepted(self) -> None:
        self.assertEqual(
            self._scan(
                "for page in range(1, 40):\n"
                "    r = requests.get(url + f'?per_page=100&page={page}')\n"
            ),
            [],
        )

    def test_the_sanctioned_reader_is_accepted(self) -> None:
        """`ci-lint cache audit` paginates and fails closed at its ceiling,
        so a repository delegating to it needs no page walk of its own."""

        self.assertEqual(
            self._scan(
                "import ci_lint.cache.audit as a\n"
                "entries = a.list_caches()  # GET /repos/{r}/actions/caches\n"
            ),
            [],
        )

    def test_per_page_alone_is_not_a_page_walk(self) -> None:
        """`?per_page=100` with no `page=` is the most common way to write
        the truncated read, so it must NOT satisfy the rule."""

        self.assertEqual(
            len(self._scan(
                "import requests\n"
                "r = requests.get('https://api.github.com/repos/o/r/actions/caches?per_page=100')\n"
            )),
            1,
        )

    def test_a_script_that_never_touches_caches_is_silent(self) -> None:
        self.assertEqual(self._scan("print('hello')\n"), [])

    def test_an_unpaginated_read_over_100_entries_undercounts_silently(self) -> None:
        """The failure this rule exists for: a short list is not an error,
        it is a plausible-looking underestimate. Measured 2026-10-05, a
        one-page read of FastLED/fbuild reported 5.81 GiB (58% of the cap)
        instead of 10.06 GiB (101%) -- a repository read as 'declare now'
        when it was already over."""

        found = self._scan(
            "import requests\n"
            "caches = requests.get('https://api.github.com/repos/o/r/actions/caches?per_page=100')"
            ".json()['actions_caches']\n"
        )
        self.assertEqual(len(found), 1)


if __name__ == "__main__":
    unittest.main()
