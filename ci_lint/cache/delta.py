"""`ci-lint cache delta manifest|pack|apply`: PR delta mechanics, pure
filesystem, stdlib `tarfile`/`json`/`hashlib` only (round-4A brief,
deliverable 6). No network, no GitHub API -- this module only ever reads
and writes local paths, so its tests need no fixtures beyond a tempdir.

- `manifest`: a sorted (relpath, size) list of a directory + a sha256
  digest of that list (issue #6 §6's self-heal diagram: "manifest ok?
  family / components / files / bytes / digest" -- a size-based integrity
  check, not a full content hash, so it stays cheap on a large restored
  cache directory).
- `pack`: only the files that are absent from a base manifest, or present
  with a different size, packed into a small `.tar.gz` whose first member
  is a JSON header naming the base digest this delta assumes plus the
  family/platform/PR it belongs to.
- `apply`: refuses (exit 3 at the CLI layer) unless the header's base
  digest equals the CURRENT base manifest's digest -- issue #6 §6: "A delta
  whose b<hash> doesn't match the restored base is discarded; that is the
  self-heal when main moves."
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path

HEADER_MEMBER_NAME = ".ci-lint-delta-header.json"


class DeltaError(ValueError):
    """Bad input (missing dir, unreadable manifest) -- CLI turns this into
    exit 2. Distinct from `StaleBaseError` (exit 3)."""


class StaleBaseError(DeltaError):
    """`apply`'s header base digest does not match the current base
    manifest's digest -- CLI turns this into exit 3 ("stale base, treat as
    miss"), per the round-4A brief."""


@dataclass(frozen=True)
class Manifest:
    entries: tuple[tuple[str, int], ...]  # sorted (relpath, size), posix separators
    digest: str  # sha256 hex of the entries, JSON-serialized

    def to_json_dict(self) -> dict[str, object]:
        return {"entries": [[p, s] for p, s in self.entries], "digest": self.digest}

    def sizes(self) -> dict[str, int]:
        return dict(self.entries)


def _digest_entries(entries: tuple[tuple[str, int], ...]) -> str:
    payload = json.dumps([[p, s] for p, s in entries], separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_manifest(root: Path) -> Manifest:
    if not root.is_dir():
        raise DeltaError(f"--dir {root} is not a directory")
    entries: list[tuple[str, int]] = []
    for path in root.rglob("*"):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            entries.append((rel, path.stat().st_size))
    entries.sort()
    entries_t = tuple(entries)
    return Manifest(entries=entries_t, digest=_digest_entries(entries_t))


def manifest_to_json(manifest: Manifest) -> str:
    return json.dumps(manifest.to_json_dict(), indent=2)


def manifest_from_json(text: str) -> Manifest:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DeltaError(f"not valid manifest JSON: {exc}") from exc
    if not isinstance(raw, dict) or "entries" not in raw or "digest" not in raw:
        raise DeltaError("manifest JSON must be an object with 'entries' and 'digest'")
    entries_raw = raw["entries"]
    if not isinstance(entries_raw, list):
        raise DeltaError("manifest 'entries' must be a list")
    entries: list[tuple[str, int]] = []
    for item in entries_raw:
        if not (isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], int)):
            raise DeltaError(f"manifest 'entries' item is not [path, size]: {item!r}")
        entries.append((item[0], item[1]))
    digest = raw["digest"]
    if not isinstance(digest, str):
        raise DeltaError("manifest 'digest' must be a string")
    return Manifest(entries=tuple(entries), digest=digest)


def load_manifest(path: Path) -> Manifest:
    if not path.is_file():
        raise DeltaError(f"manifest not found: {path}")
    return manifest_from_json(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class DeltaHeader:
    base_digest: str
    family: str
    platform: str
    pr: int

    def to_json_dict(self) -> dict[str, object]:
        return {"base_digest": self.base_digest, "family": self.family, "platform": self.platform, "pr": self.pr}


def _changed_files(root: Path, base: Manifest) -> list[str]:
    base_sizes = base.sizes()
    changed: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        size = path.stat().st_size
        if base_sizes.get(rel) != size:
            changed.append(rel)
    return changed


def pack(root: Path, base_manifest: Manifest, out: Path, *, family: str, platform: str, pr: int) -> tuple[str, ...]:
    """Write `out` (`.tar.gz`): a header member (`DeltaHeader`) plus every
    file under `root` that's absent from `base_manifest` or has a
    different size. Returns the packed relpaths."""

    if not root.is_dir():
        raise DeltaError(f"--dir {root} is not a directory")
    changed = _changed_files(root, base_manifest)
    header = DeltaHeader(base_digest=base_manifest.digest, family=family, platform=platform, pr=pr)
    header_bytes = json.dumps(header.to_json_dict(), separators=(",", ":")).encode("utf-8")

    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        header_info = tarfile.TarInfo(name=HEADER_MEMBER_NAME)
        header_info.size = len(header_bytes)
        tar.addfile(header_info, io.BytesIO(header_bytes))
        for rel in changed:
            tar.add(root / rel, arcname=rel)
    return tuple(changed)


def _read_header(tar: tarfile.TarFile) -> DeltaHeader:
    try:
        member = tar.getmember(HEADER_MEMBER_NAME)
    except KeyError as exc:
        raise DeltaError(f"delta archive has no {HEADER_MEMBER_NAME!r} header member") from exc
    fh = tar.extractfile(member)
    if fh is None:
        raise DeltaError(f"delta archive's {HEADER_MEMBER_NAME!r} member has no content")
    try:
        raw = json.loads(fh.read().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DeltaError(f"delta header is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise DeltaError("delta header JSON must be an object")
    try:
        return DeltaHeader(
            base_digest=str(raw["base_digest"]), family=str(raw["family"]), platform=str(raw["platform"]), pr=int(raw["pr"])
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DeltaError(f"delta header missing/malformed field: {exc}") from exc


def read_header(delta_path: Path) -> DeltaHeader:
    if not delta_path.is_file():
        raise DeltaError(f"--delta {delta_path} not found")
    with tarfile.open(delta_path, "r:gz") as tar:
        return _read_header(tar)


def apply(root: Path, delta_path: Path, base_manifest: Manifest) -> tuple[str, ...]:
    """Verify the delta's header base digest equals `base_manifest.digest`
    (else `StaleBaseError`, CLI exit 3: "stale base, treat as miss"), then
    extract every non-header member into `root`, overlaying the base
    restore already there. Returns the extracted relpaths."""

    if not delta_path.is_file():
        raise DeltaError(f"--delta {delta_path} not found")
    root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(delta_path, "r:gz") as tar:
        header = _read_header(tar)
        if header.base_digest != base_manifest.digest:
            raise StaleBaseError(
                f"delta's base digest {header.base_digest} != current base manifest digest "
                f"{base_manifest.digest}"
            )
        extracted: list[str] = []
        for member in tar.getmembers():
            if member.name == HEADER_MEMBER_NAME:
                continue
            try:
                tar.extract(member, path=root, filter="data")
            except TypeError:
                # `filter=` was backported to some 3.11.x point releases but
                # not all (python/cpython gh-91048); fall back for an older
                # one rather than requiring a specific patch version.
                tar.extract(member, path=root)
            if member.isfile():
                extracted.append(member.name)
    return tuple(extracted)
