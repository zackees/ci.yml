"""`ci-lint cache delta manifest|pack|apply` -- ci_lint.cache.delta
(round-4A brief, deliverable 6). Pure filesystem, stdlib tarfile/zlib; no
network, no ci.toml -- every test works in a tempdir.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ci_lint.cache.delta import (
    DeltaError,
    StaleBaseError,
    apply,
    build_manifest,
    manifest_from_json,
    manifest_to_json,
    pack,
)


class ManifestTest(unittest.TestCase):
    def test_sorted_relpath_size_list_and_a_stable_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b.txt").write_bytes(b"bb")
            (root / "a.txt").write_bytes(b"a")
            (root / "sub").mkdir()
            (root / "sub" / "c.txt").write_bytes(b"ccc")

            m1 = build_manifest(root)
            self.assertEqual((("a.txt", 1), ("b.txt", 2), ("sub/c.txt", 3)), m1.entries)

            # re-running against an identical tree is deterministic.
            m2 = build_manifest(root)
            self.assertEqual(m1.digest, m2.digest)

    def test_digest_changes_when_a_size_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_bytes(b"a")
            m1 = build_manifest(root)
            (root / "a.txt").write_bytes(b"aa")
            m2 = build_manifest(root)
            self.assertNotEqual(m1.digest, m2.digest)

    def test_json_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_bytes(b"a")
            m = build_manifest(root)
            restored = manifest_from_json(manifest_to_json(m))
            self.assertEqual(m, restored)

    def test_missing_dir_is_a_delta_error(self) -> None:
        with self.assertRaises(DeltaError):
            build_manifest(Path("/does/not/exist"))


class PackApplyRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.base = self.root / "base"
        self.base.mkdir()
        (self.base / "f1.txt").write_bytes(b"aaa")
        (self.base / "f2.txt").write_bytes(b"bbb")
        self.base_manifest = build_manifest(self.base)

    def test_pack_includes_only_absent_or_changed_files(self) -> None:
        pr = self.root / "pr"
        pr.mkdir()
        (pr / "f1.txt").write_bytes(b"aaa")  # unchanged
        (pr / "f2.txt").write_bytes(b"changed")  # different size
        (pr / "f3.txt").write_bytes(b"new")  # absent from base

        out = self.root / "delta.tar.gz"
        changed = pack(pr, self.base_manifest, out, family="compile", platform="linux-x64", pr=1)
        self.assertEqual(("f2.txt", "f3.txt"), changed)
        self.assertTrue(out.is_file())

    def test_apply_overlays_changed_files_onto_a_restored_base_and_leaves_unchanged_ones(self) -> None:
        pr = self.root / "pr"
        pr.mkdir()
        (pr / "f1.txt").write_bytes(b"aaa")
        (pr / "f2.txt").write_bytes(b"changed")
        (pr / "f3.txt").write_bytes(b"new")
        out = self.root / "delta.tar.gz"
        pack(pr, self.base_manifest, out, family="compile", platform="linux-x64", pr=1)

        restore = self.root / "restore"
        restore.mkdir()
        (restore / "f1.txt").write_bytes(b"aaa")
        (restore / "f2.txt").write_bytes(b"bbb")

        extracted = apply(restore, out, self.base_manifest)
        self.assertEqual({"f2.txt", "f3.txt"}, set(extracted))
        self.assertEqual(b"aaa", (restore / "f1.txt").read_bytes())  # untouched
        self.assertEqual(b"changed", (restore / "f2.txt").read_bytes())
        self.assertEqual(b"new", (restore / "f3.txt").read_bytes())

    def test_apply_refuses_a_stale_base_with_exit_3_equivalent_error(self) -> None:
        pr = self.root / "pr"
        pr.mkdir()
        (pr / "f2.txt").write_bytes(b"changed")
        out = self.root / "delta.tar.gz"
        pack(pr, self.base_manifest, out, family="compile", platform="linux-x64", pr=1)

        other_base = self.root / "other-base"
        other_base.mkdir()
        (other_base / "f1.txt").write_bytes(b"totally different")
        other_manifest = build_manifest(other_base)

        restore = self.root / "restore"
        restore.mkdir()
        with self.assertRaises(StaleBaseError):
            apply(restore, out, other_manifest)

    def test_apply_missing_delta_file_is_a_plain_delta_error_not_stale_base(self) -> None:
        with self.assertRaises(DeltaError):
            apply(self.root / "restore", self.root / "does-not-exist.tar.gz", self.base_manifest)


if __name__ == "__main__":
    unittest.main()
