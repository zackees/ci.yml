"""ACT-001 (round M2-20, zackees/ci.yml#44 part A): `ci-lint act audit`
against a local act cache-server store. Ported from
template-python-rust-cmd's ci/localrun/cache_audit.py; this test builds a
synthetic bolt.db (one literal JSON record per index entry, exactly the
shape act's cache-server writes) rather than depending on a real act
binary."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ci_lint.act_audit import audit


def _record(rid: int, key: str, size: int, *, complete: bool = True, used_at: int = 1700000000) -> bytes:
    obj = {
        "id": rid,
        "key": key,
        "version": "v1",
        "cacheSize": size,
        "complete": complete,
        "usedAt": used_at,
        "createdAt": used_at,
    }
    # Match the exact key order _RECORD_RE expects.
    return (
        '{"id":%d,"key":"%s","version":"%s","cacheSize":%d,"complete":%s,"usedAt":%d,"createdAt":%d}'
        % (obj["id"], obj["key"], obj["version"], obj["cacheSize"],
           "true" if obj["complete"] else "false", obj["usedAt"], obj["createdAt"])
    ).encode()


class ActAuditTest(unittest.TestCase):
    def test_red_retired_family_key_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = Path(td)
            (store / "bolt.db").write_bytes(_record(1, "solo-toolchain-v3-linux-abc123", 10 * 1024 * 1024))
            result = audit(store, budget_bytes=9 * 1024**3, retired_families=("solo-toolchain", "cook-delta-v2"))
            self.assertFalse(result.ok)
            self.assertTrue(any("ACT-001" in f for f in result.findings))
            self.assertEqual(1, len(result.retired_hits))

    def test_green_no_retired_key_within_budget(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = Path(td)
            (store / "bolt.db").write_bytes(_record(1, "uv-v1-linux-x64-abc123", 10 * 1024 * 1024))
            result = audit(store, budget_bytes=9 * 1024**3, retired_families=("solo-toolchain", "cook-delta-v2"))
            self.assertTrue(result.ok)
            self.assertEqual((), result.findings)

    def test_over_budget_is_flagged_via_real_blob_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = Path(td)
            (store / "bolt.db").write_bytes(_record(1, "uv-v1-linux-x64-abc123", 1))
            blobs = store / "cache"
            blobs.mkdir()
            (blobs / "big.bin").write_bytes(b"0" * 2048)
            result = audit(store, budget_bytes=1024, retired_families=())
            self.assertFalse(result.ok)
            self.assertTrue(any("over ci.toml" in f for f in result.findings))

    def test_incomplete_record_never_counted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = Path(td)
            (store / "bolt.db").write_bytes(_record(1, "solo-toolchain-v3-linux-abc", 10, complete=False))
            result = audit(store, budget_bytes=9 * 1024**3, retired_families=("solo-toolchain",))
            self.assertEqual((), result.entries)
            self.assertTrue(result.ok)

    def test_missing_store_is_empty_and_ok(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "does-not-exist"
            result = audit(store, budget_bytes=9 * 1024**3, retired_families=())
            self.assertTrue(result.ok)
            self.assertEqual((), result.entries)


if __name__ == "__main__":
    unittest.main()
