"""`ci-lint cache delta manifest|pack|apply` -- ci_lint.cache.delta
(round-4A brief, deliverable 6). Pure filesystem, stdlib tarfile/zlib; no
network, no ci.toml -- every test works in a tempdir.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from ci_lint.cache.delta import (
    HEADER_MEMBER_NAME,
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
            self.assertEqual(
                (("a.txt", 1), ("b.txt", 2), ("sub/c.txt", 3)), tuple((entry.path, entry.size) for entry in m1.entries)
            )

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

    def test_digest_changes_when_equal_size_content_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pointer.current").write_bytes(b"old")
            before = build_manifest(root)
            (root / "pointer.current").write_bytes(b"new")
            self.assertNotEqual(before.digest, build_manifest(root).digest)

    def test_json_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_bytes(b"a")
            m = build_manifest(root)
            restored = manifest_from_json(manifest_to_json(m))
            self.assertEqual(m, restored)

    def test_legacy_size_only_manifest_requires_regeneration(self) -> None:
        with self.assertRaisesRegex(DeltaError, "regenerate"):
            manifest_from_json(json.dumps({"entries": [["a", 1]], "digest": "a" * 64}))

    def test_changed_content_identity_with_unchanged_digest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").write_bytes(b"a")
            document = json.loads(manifest_to_json(build_manifest(root)))
            document["entries"][0][2] = "b" * 64
            with self.assertRaisesRegex(DeltaError, "digest does not match"):
                manifest_from_json(json.dumps(document))

    def test_nested_paths_round_trip_in_wire_sort_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").mkdir()
            (root / "a" / "file").write_bytes(b"a")
            (root / "a.txt").write_bytes(b"b")
            manifest = build_manifest(root)
            self.assertEqual(manifest, manifest_from_json(manifest_to_json(manifest)))

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

    def test_equal_size_generation_pointer_survives_fresh_restore(self) -> None:
        # ci.yml#362: zccache staged-v2 generations use fixed-length .current
        # pointers. Publishing new objects alone leaves the old generation live.
        key, old_generation, new_generation = "a" * 64, "b" * 64, "c" * 64
        pointer = f".staged-v2/{key}.current"
        old_payload = f".staged-v2/{key}/{old_generation}/output-0"
        new_payload = f".staged-v2/{key}/{new_generation}/output-0"
        for name, content in ((pointer, old_generation.encode()), (old_payload, b"old object")):
            path = self.base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        base_manifest = build_manifest(self.base)
        pr = self.root / "pr"
        for name, content in (
            (pointer, new_generation.encode()),
            (old_payload, b"old object"),
            (new_payload, b"new object"),
        ):
            path = pr / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        archive = self.root / "generation.tar.gz"
        changed = pack(pr, base_manifest, archive, family="compile", platform="linux-x64", pr=1)
        self.assertIn(pointer, changed)
        restored = self.root / "fresh-engine"
        for name, content in ((pointer, old_generation.encode()), (old_payload, b"old object")):
            path = restored / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        apply(restored, archive, base_manifest)
        selected = (restored / pointer).read_text()
        self.assertEqual(new_generation, selected)
        self.assertEqual(b"new object", (restored / f".staged-v2/{key}/{selected}/output-0").read_bytes())

    def test_equal_size_different_base_refuses_delta(self) -> None:
        pr = self.root / "pr"
        pr.mkdir()
        (pr / "new.txt").write_bytes(b"new")
        archive = self.root / "delta.tar.gz"
        pack(pr, self.base_manifest, archive, family="compile", platform="linux-x64", pr=1)
        (self.base / "f1.txt").write_bytes(b"xxx")
        changed_base = build_manifest(self.base)
        with self.assertRaises(StaleBaseError):
            apply(self.root / "restored", archive, changed_base)

    def test_old_size_only_archive_is_stale_even_for_empty_base(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        manifest = build_manifest(empty)
        header = json.dumps(
            {
                "base_digest": hashlib.sha256(b"[]").hexdigest(),
                "family": "compile",
                "platform": "linux-x64",
                "pr": 1,
            }
        ).encode()
        archive = self.root / "old-delta.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            member = tarfile.TarInfo(HEADER_MEMBER_NAME)
            member.size = len(header)
            tar.addfile(member, io.BytesIO(header))
        with self.assertRaises(StaleBaseError):
            apply(empty, archive, manifest)

    def test_apply_missing_delta_file_is_a_plain_delta_error_not_stale_base(self) -> None:
        with self.assertRaises(DeltaError):
            apply(self.root / "restore", self.root / "does-not-exist.tar.gz", self.base_manifest)


if __name__ == "__main__":
    unittest.main()
