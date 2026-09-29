"""`ci-lint release verify`: issue #6 §3/§7 + ci.yml#4's release-candidate
gating -- the staged artifact set for an exact candidate SHA must contain
exactly one wheel per declared [platforms] entry (matched by its wheel
platform tag, not by filename guesswork) plus exactly one sdist, every
artifact at the same version, and every wheel passing the existing `wheel
check` (PKG-003/004/005, reused from `ci_lint.runtime.wheel` rather than
re-implemented). Writes `release-manifest.json` (sha256 per artifact, the
candidate SHA, and ci.toml's own digest) into the staged directory.

PKG-006 covers a missing/duplicate/extra artifact or a version mismatch.
Per-wheel native install smoke happens elsewhere (the platform-run job);
this command only *consumes* a `--smoke <dir>` of already-written
`smoke-results/*.json` files and requires a passing one per wheel -- it
never runs an installer itself (stdlib-only, no subprocess here).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from ci_lint.finding import Finding, Status
from ci_lint.runtime.wheel import WheelFilenameParts, check_wheel, parse_wheel_filename
from ci_lint.schema import CiToml, Platform

RELEASE_MANIFEST_SCHEMA_VERSION = 1
SDIST_RE = re.compile(r"^(?P<name>.+)-(?P<version>[^-]+)\.tar\.gz$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class ReleaseVerifyError(Exception):
    """Bad input (missing --dist, unreadable ci.toml, ...) -- exit 2."""


@dataclass(frozen=True)
class SdistParts:
    name: str
    version: str


def parse_sdist_filename(path: Path) -> SdistParts | None:
    m = SDIST_RE.match(path.name)
    if m is None:
        return None
    return SdistParts(name=m.group("name"), version=m.group("version"))


def expected_wheel_tag_pattern(platform: Platform) -> re.Pattern[str] | None:
    """Derives the expected wheel `platform_tag` regex for a declared
    [platforms.<id>] entry from its `group` and the CPU architecture in its
    `target` triple -- the round-5 brief's exact mapping: manylinux_2_17_
    x86_64/aarch64, win_amd64, win_arm64, macosx_*_arm64, macosx_*_x86_64.
    Returns None for a group this function doesn't know how to map (caller
    reports that as needs_review, never silently skips the platform)."""

    arch = platform.target.split("-", 1)[0] if platform.target else ""
    if platform.group == "linux":
        base = platform.wheel or "manylinux_2_17"
        return re.compile(rf"^{re.escape(base)}_{re.escape(arch)}$")
    if platform.group == "windows":
        win_arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(arch)
        if win_arch is None:
            return None
        return re.compile(rf"^win_{win_arch}$")
    if platform.group == "macos":
        mac_arch = {"aarch64": "arm64", "x86_64": "x86_64"}.get(arch)
        if mac_arch is None:
            return None
        return re.compile(rf"^macosx_\d+_\d+_{mac_arch}$")
    return None


@dataclass(frozen=True)
class StagedWheel:
    path: Path
    parts: WheelFilenameParts
    platform_id: str | None  # None: matched no declared platform


@dataclass(frozen=True)
class StagedArtifacts:
    wheels: tuple[StagedWheel, ...]
    sdist: tuple[Path, SdistParts] | None
    unparsed: tuple[Path, ...]  # .whl/.tar.gz files this module could not parse at all


def discover_staged_artifacts(dist_dir: Path) -> StagedArtifacts:
    if not dist_dir.is_dir():
        raise ReleaseVerifyError(f"--dist {dist_dir} is not a directory")
    wheels: list[StagedWheel] = []
    sdist: tuple[Path, SdistParts] | None = None
    sdists_found: list[tuple[Path, SdistParts]] = []
    unparsed: list[Path] = []
    for entry in sorted(dist_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix == ".whl":
            parts = parse_wheel_filename(entry)
            if parts is None:
                unparsed.append(entry)
            else:
                wheels.append(StagedWheel(path=entry, parts=parts, platform_id=None))
        elif entry.name.endswith(".tar.gz"):
            sd_parts = parse_sdist_filename(entry)
            if sd_parts is None:
                unparsed.append(entry)
            else:
                sdists_found.append((entry, sd_parts))
    if len(sdists_found) == 1:
        sdist = sdists_found[0]
    return StagedArtifacts(wheels=tuple(wheels), sdist=sdist, unparsed=tuple(unparsed))


def _match_platform(wheel: StagedWheel, patterns: dict[str, re.Pattern[str]]) -> str | None:
    for platform_id, pattern in patterns.items():
        if pattern.match(wheel.parts.platform_tag):
            return platform_id
    return None


@dataclass(frozen=True)
class ArtifactRecord:
    path: str
    kind: str  # "wheel" | "sdist"
    platform: str | None
    version: str
    sha256: str


@dataclass(frozen=True)
class ReleaseVerifyReport:
    findings: tuple[Finding, ...]
    artifacts: tuple[ArtifactRecord, ...]
    version: str | None
    candidate_sha: str
    ci_toml_digest: str


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pkg006(message: str, fix: str, path: str) -> Finding:
    return Finding(rule="PKG-006", path=path, message=message, fix=fix)


def verify_staged_artifacts(
    ci: CiToml,
    dist_dir: Path,
    *,
    candidate_sha: str,
    ci_toml_digest: str,
    smoke_dir: Path | None = None,
) -> ReleaseVerifyReport:
    if not SHA_RE.match(candidate_sha):
        raise ReleaseVerifyError(f"--sha {candidate_sha!r} is not a 40-hex-char commit SHA")
    if ci.python is None:
        raise ReleaseVerifyError("ci.toml has no [python] table -- required for a rust-pypi-app release")

    findings: list[Finding] = []
    staged = discover_staged_artifacts(dist_dir)

    patterns: dict[str, re.Pattern[str]] = {}
    for pid, platform in ci.platforms.items():
        pattern = expected_wheel_tag_pattern(platform)
        if pattern is None:
            findings.append(
                Finding(
                    rule="PKG-006",
                    status=Status.NEEDS_REVIEW,
                    path=f"platforms.{pid}",
                    message=f"platform '{pid}' has group {platform.group!r} / target {platform.target!r}, "
                    "which this command does not know how to map to a wheel platform tag",
                    fix="extend ci_lint.release.expected_wheel_tag_pattern for this group/arch, or "
                    "confirm this platform is not expected to ship its own wheel",
                )
            )
            continue
        patterns[pid] = pattern

    matched: dict[str, list[StagedWheel]] = {pid: [] for pid in patterns}
    unmatched_wheels: list[StagedWheel] = []
    for wheel in staged.wheels:
        pid = _match_platform(wheel, patterns)
        if pid is None:
            unmatched_wheels.append(wheel)
        else:
            matched[pid].append(StagedWheel(path=wheel.path, parts=wheel.parts, platform_id=pid))

    for pid, pattern in patterns.items():
        count = len(matched[pid])
        if count == 0:
            findings.append(
                _pkg006(
                    f"no staged wheel matches platform '{pid}' (expected platform_tag matching "
                    f"{pattern.pattern!r})",
                    f"build and stage a wheel for '{pid}' before this candidate can be released",
                    f"platforms.{pid}",
                )
            )
        elif count > 1:
            names = ", ".join(w.path.name for w in matched[pid])
            findings.append(
                _pkg006(
                    f"{count} staged wheels match platform '{pid}': {names}",
                    f"stage exactly one wheel per platform; remove the duplicate build(s) for '{pid}'",
                    f"platforms.{pid}",
                )
            )

    for wheel in unmatched_wheels:
        findings.append(
            _pkg006(
                f"staged wheel '{wheel.path.name}' (platform_tag={wheel.parts.platform_tag!r}) matches "
                "no declared [platforms] entry",
                "remove this extra wheel from the staged set, or declare the platform it corresponds to "
                "in ci.toml's [platforms]",
                wheel.path.name,
            )
        )

    for extra in staged.unparsed:
        findings.append(
            _pkg006(
                f"'{extra.name}' in the staged set is not a well-formed wheel/sdist filename",
                "rebuild it with 'soldr wheel'/'uv build --sdist' so it is named per PEP 427/625, or "
                "remove it from the staged directory",
                extra.name,
            )
        )

    if staged.sdist is None:
        sdist_candidates = [p for p in dist_dir.glob("*.tar.gz")]
        if not sdist_candidates:
            findings.append(_pkg006("no sdist (*.tar.gz) in the staged set", "run 'uv build --sdist'", "sdist"))
        else:
            findings.append(
                _pkg006(
                    f"{len(sdist_candidates)} sdist files staged, expected exactly 1: "
                    f"{', '.join(p.name for p in sdist_candidates)}",
                    "stage exactly one sdist",
                    "sdist",
                )
            )

    # Version consistency across every staged (parseable) artifact.
    versions: set[str] = {w.parts.version for w in staged.wheels}
    if staged.sdist is not None:
        versions.add(staged.sdist[1].version)
    version: str | None = None
    if len(versions) > 1:
        findings.append(
            _pkg006(
                f"staged artifacts declare inconsistent versions: {sorted(versions)}",
                "rebuild every artifact from the same tagged commit/version so they all agree",
                "version",
            )
        )
    elif len(versions) == 1:
        version = next(iter(versions))

    # Reuse the existing wheel check (PKG-003/004/005) per staged wheel;
    # the sdist backend check only needs to run once (it reads the same
    # pyproject.toml regardless of which wheel it's paired with).
    sdist_path = staged.sdist[0] if staged.sdist is not None else None
    all_staged_wheels = [w for lst in matched.values() for w in lst]
    for i, wheel in enumerate(all_staged_wheels):
        report = check_wheel(ci, wheel.path, sdist_path if i == 0 else None)
        findings.extend(report.findings)

    if smoke_dir is not None:
        findings.extend(_verify_smoke(smoke_dir, all_staged_wheels))

    artifacts: list[ArtifactRecord] = []
    for wheel in all_staged_wheels:
        artifacts.append(
            ArtifactRecord(
                path=wheel.path.name,
                kind="wheel",
                platform=wheel.platform_id,
                version=wheel.parts.version,
                sha256=_sha256_file(wheel.path),
            )
        )
    if staged.sdist is not None:
        path, parts = staged.sdist
        artifacts.append(
            ArtifactRecord(path=path.name, kind="sdist", platform=None, version=parts.version, sha256=_sha256_file(path))
        )
    artifacts.sort(key=lambda a: a.path)

    return ReleaseVerifyReport(
        findings=tuple(findings),
        artifacts=tuple(artifacts),
        version=version,
        candidate_sha=candidate_sha.lower(),
        ci_toml_digest=ci_toml_digest,
    )


@dataclass(frozen=True)
class SmokeResult:
    platform: str
    wheel: str
    passed: bool
    detail: str | None


def load_smoke_results(smoke_dir: Path) -> list[SmokeResult]:
    results: list[SmokeResult] = []
    for path in sorted(smoke_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        platform = raw.get("platform")
        wheel = raw.get("wheel")
        passed = raw.get("passed")
        detail = raw.get("detail")
        if not isinstance(platform, str) or not isinstance(wheel, str) or not isinstance(passed, bool):
            continue
        results.append(
            SmokeResult(platform=platform, wheel=wheel, passed=passed, detail=detail if isinstance(detail, str) else None)
        )
    return results


def _verify_smoke(smoke_dir: Path, wheels: list[StagedWheel]) -> list[Finding]:
    if not smoke_dir.is_dir():
        return [
            Finding(
                rule="PKG-006",
                status=Status.NEEDS_REVIEW,
                path=str(smoke_dir),
                message=f"--smoke {smoke_dir} is not a directory",
                fix="point --smoke at the directory the platform-run job wrote smoke-results/*.json into",
            )
        ]
    results = load_smoke_results(smoke_dir)
    by_wheel: dict[str, SmokeResult] = {r.wheel: r for r in results}
    findings: list[Finding] = []
    for wheel in wheels:
        result = by_wheel.get(wheel.path.name)
        if result is None:
            findings.append(
                Finding(
                    rule="PKG-006",
                    path=wheel.path.name,
                    message=f"no smoke result for '{wheel.path.name}' in {smoke_dir}",
                    fix="the platform-run job must write a smoke-results/<wheel>.json for every staged "
                    "wheel before release verify can pass",
                )
            )
        elif not result.passed:
            findings.append(
                Finding(
                    rule="PKG-006",
                    path=wheel.path.name,
                    message=f"native install smoke failed for '{wheel.path.name}'"
                    + (f": {result.detail}" if result.detail else ""),
                    fix="fix the failing platform's install/CLI smoke before this candidate can release",
                )
            )
    return findings


def write_release_manifest(report: ReleaseVerifyReport, dist_dir: Path) -> Path:
    payload = {
        "schema_version": RELEASE_MANIFEST_SCHEMA_VERSION,
        "candidate_sha": report.candidate_sha,
        "ci_toml_digest": report.ci_toml_digest,
        "version": report.version,
        "artifacts": [
            {"path": a.path, "kind": a.kind, "platform": a.platform, "version": a.version, "sha256": a.sha256}
            for a in report.artifacts
        ],
    }
    out_path = dist_dir / "release-manifest.json"
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out_path


def to_json_dict(report: ReleaseVerifyReport) -> dict[str, object]:
    return {
        "candidate_sha": report.candidate_sha,
        "ci_toml_digest": report.ci_toml_digest,
        "version": report.version,
        "artifacts": [
            {"path": a.path, "kind": a.kind, "platform": a.platform, "version": a.version, "sha256": a.sha256}
            for a in report.artifacts
        ],
        "findings": [
            {"rule": f.rule, "status": f.status.value, "path": f.path, "message": f.message, "fix": f.fix}
            for f in report.findings
        ],
    }


def render_text(report: ReleaseVerifyReport) -> str:
    lines = [f"ci-lint release verify: candidate {report.candidate_sha} (version {report.version or '?'})"]
    lines.append(f"{'artifact':<60} {'kind':<8} {'platform':<14} sha256")
    for a in report.artifacts:
        lines.append(f"{a.path:<60} {a.kind:<8} {a.platform or '-':<14} {a.sha256[:16]}...")
    lines.append("")
    if not report.findings:
        lines.append("ci-lint release verify: no findings.")
    for f in report.findings:
        lines.append(f.render())
    n = sum(1 for f in report.findings if f.status == Status.VIOLATION)
    lines.append(f"ci-lint release verify: {n} violation(s)")
    return "\n".join(lines)
