"""CT-007: a `linter` pin must name a commit that exists."""

from __future__ import annotations

import unittest

from ci_lint.rules.linter_pin import check_ct_007, resolve_linter_pin

DEAD = "fe965fc7e2c5203570072e1d0fd19d0935d98e39"
LIVE = "92106df315bd28de24ef1808c893c91a5859122d"


def _fetch(status: int):
    def inner(url: str, token: str) -> tuple[int, object]:
        return status, {"sha": LIVE} if status == 200 else {"message": "nope"}

    return inner


class LinterPinTest(unittest.TestCase):
    def test_dead_pin_is_a_violation(self) -> None:
        result = resolve_linter_pin(_fetch(422), "t", "zackees/bosn", DEAD)
        self.assertIs(result.resolves, False)
        self.assertIn("422", result.detail)

    def test_live_pin_resolves(self) -> None:
        result = resolve_linter_pin(_fetch(200), "t", "zackees/reld", LIVE)
        self.assertIs(result.resolves, True)

    def test_unauthenticated_401_is_not_a_dead_pin(self) -> None:
        """A 401 answers nothing: GitHub rejects EVERY commit without
        credentials, live or dead. Reporting that as a dead pin would accuse a
        repository on the strength of a missing token -- the same failure shape
        as CACHE-034's unreadable-workflow case, and one this rule hit in its
        own first draft."""

        result = resolve_linter_pin(_fetch(401), "", "zackees/reld", LIVE)
        self.assertIsNone(result.resolves)

    def test_transport_failure_is_unknown_not_dead(self) -> None:
        def boom(url: str, token: str) -> tuple[int, object]:
            raise OSError("connection reset")

        self.assertIsNone(resolve_linter_pin(boom, "t", "zackees/reld", LIVE).resolves)

    def test_offline_run_stays_silent(self) -> None:
        """Resolvability is remote state. With no fetch there is no evidence,
        so the rule says nothing rather than guessing (CT-002 covers shape)."""

        class FakeCi:
            linter_sha = DEAD

        self.assertEqual(check_ct_007(FakeCi(), repo="zackees/bosn"), [])  # type: ignore[arg-type]

    def test_check_reports_a_violation_for_a_dead_pin(self) -> None:
        class FakeCi:
            linter_sha = DEAD

        findings = check_ct_007(
            FakeCi(), fetch_status=_fetch(422), token="t", repo="zackees/bosn"  # type: ignore[arg-type]
        )
        self.assertEqual([f.rule for f in findings], ["CT-007"])
        self.assertEqual(findings[0].status.value, "violation")
        self.assertIn(DEAD, findings[0].message)
        self.assertIn("GATE-003", findings[0].message)

    def test_check_is_silent_for_a_live_pin(self) -> None:
        class FakeCi:
            linter_sha = LIVE

        self.assertEqual(
            check_ct_007(  # type: ignore[arg-type]
                FakeCi(), fetch_status=_fetch(200), token="t", repo="zackees/reld"
            ),
            [],
        )

    def test_no_pin_declared_is_silent(self) -> None:
        class FakeCi:
            linter_sha = None

        self.assertEqual(check_ct_007(FakeCi(), fetch_status=_fetch(422), repo="x"), [])  # type: ignore[arg-type]
