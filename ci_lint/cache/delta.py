"""`ci-lint cache delta manifest|pack|apply`: PR delta mechanics, pure
filesystem, stdlib `tarfile`/`json`/`hashlib` only (round-4A brief,
deliverable 6). No network, no GitHub API -- this module only ever reads
and writes local paths, so its tests need no fixtures beyond a tempdir.

- `manifest`: schema-2 content identities (path, size, SHA-256) plus a
  digest of the sorted list. Same-size pointer and metadata updates matter.
- `pack`: only the files that are absent from a base manifest, or present
  with different contents, packed into a small `.tar.gz` whose first member
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
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path

from ci_lint.cargo_messages import JsonValue

HEADER_MEMBER_NAME = ".ci-lint-delta-header.json"
MANIFEST_SCHEMA = 2


class DeltaError(ValueError):
    """Bad input (missing dir, unreadable manifest) -- CLI turns this into
    exit 2. Distinct from `StaleBaseError` (exit 3)."""


class StaleBaseError(DeltaError):
    """`apply`'s header base digest does not match the current base
    manifest's digest -- CLI turns this into exit 3 ("stale base, treat as
    miss"), per the round-4A brief."""


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Manifest:
    entries: tuple[ManifestEntry, ...]
    digest: str

    def to_json_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": MANIFEST_SCHEMA,
            "entries": [[entry.path, entry.size, entry.sha256] for entry in self.entries],
            "digest": self.digest,
        }


def _digest_entries(entries: tuple[ManifestEntry, ...]) -> str:
    payload = json.dumps(
        {"schema_version": MANIFEST_SCHEMA, "entries": [[entry.path, entry.size, entry.sha256] for entry in entries]},
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build_manifest(root: Path) -> Manifest:
    if not root.is_dir():
        raise DeltaError(f"--dir {root} is not a directory")
    entries = tuple(
        sorted(
            (
                ManifestEntry(path.relative_to(root).as_posix(), path.stat().st_size, _file_digest(path))
                for path in root.rglob("*")
                if path.is_file()
            ),
            key=lambda entry: entry.path,
        )
    )
    return Manifest(entries=entries, digest=_digest_entries(entries))


def manifest_to_json(manifest: Manifest) -> str:
    return json.dumps(manifest.to_json_dict(), indent=2)


def _manifest_entry(item: object) -> ManifestEntry:
    if not isinstance(item, list) or len(item) != 3:
        raise DeltaError("manifest entry must be [path, size, sha256]")
    path, size, digest = item
    if not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts:
        raise DeltaError("manifest entry path must be a nonempty relative path")
    if type(size) is not int or size < 0:
        raise DeltaError("manifest entry size must be a nonnegative integer")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise DeltaError("manifest entry sha256 must be a lowercase SHA-256 digest")
    return ManifestEntry(path, size, digest)


def manifest_from_json(text: str) -> Manifest:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DeltaError(f"not valid manifest JSON: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != MANIFEST_SCHEMA:
        raise DeltaError("manifest requires content schema 2; regenerate size-only manifests")
    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list):
        raise DeltaError("manifest 'entries' must be a list")
    entries = tuple(_manifest_entry(item) for item in entries_raw)
    paths = [entry.path for entry in entries]
    if paths != sorted(set(paths)):
        raise DeltaError("manifest entries must be sorted and have unique paths")
    digest = _digest_entries(entries)
    if raw.get("digest") != digest:
        raise DeltaError("manifest digest does not match its content identities")
    return Manifest(entries, digest)


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

    def to_json_dict(self) -> dict[str, JsonValue]:
        return {"base_digest": self.base_digest, "family": self.family, "platform": self.platform, "pr": self.pr}


def _changed_files(root: Path, base: Manifest) -> list[str]:
    base_digests = {entry.path: entry.sha256 for entry in base.entries}
    return sorted(
        [
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and base_digests.get(path.relative_to(root).as_posix()) != _file_digest(path)
        ]
    )


def pack(root: Path, base_manifest: Manifest, out: Path, *, family: str, platform: str, pr: int) -> tuple[str, ...]:
    """Write `out` (`.tar.gz`): a header member (`DeltaHeader`) plus every
    file under `root` that's absent from `base_manifest` or has
    different contents. Returns the packed relpaths."""

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
            base_digest=str(raw["base_digest"]),
            family=str(raw["family"]),
            platform=str(raw["platform"]),
            pr=int(raw["pr"]),
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
                f"delta's base digest {header.base_digest} != current base manifest digest {base_manifest.digest}"
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
