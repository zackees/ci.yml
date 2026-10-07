"""Exact-key cleanup in the act2 namespace; never fall back to GitHub."""

import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass

from ci_lint.cargo_messages import JsonValue
from ci_lint.workflow_replay_outputs import unique_json_object


@dataclass(frozen=True)
class LocalHealReceipt:
    schema_version: int
    key: str
    deleted_count: int
    reclaimed_archive_bytes: int
    dry_run: bool = False
    backend: str = "act2"

    def to_json_dict(self) -> dict[str, JsonValue]:
        return asdict(self)

    def render(self) -> str:
        action = "would delete" if self.dry_run else f"deleted {self.deleted_count} completed entrie(s) for"
        return f"ci-lint cache heal: act2 {action} exact key {self.key!r}; {self.reclaimed_archive_bytes} archive bytes"


class LocalHealError(ValueError):
    """A local cleanup failed; diagnostics never include the URL's secret token."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _endpoint(base: str, key: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(base)
        host = ipaddress.ip_address(parsed.hostname or "")
        valid = (parsed.scheme == "http" and (host.is_private or host.is_loopback)
                 and parsed.port is not None and parsed.username is None and parsed.password is None
                 and not parsed.query and not parsed.fragment
                 and re.fullmatch(r"/[0-9a-f]{32}/?", parsed.path) is not None)
    except ValueError:
        valid = False
    if not valid:
        raise LocalHealError("act2 cleanup requires its local token-bound ACTIONS_CACHE_URL")
    return base.rstrip("/") + "/_apis/artifactcache/cache?" + urllib.parse.urlencode({"key": key})


def _receipt(payload: JsonValue, key: str) -> LocalHealReceipt:
    if (not isinstance(payload, dict) or type(payload.get("schema_version")) is not int
            or payload["schema_version"] != 1 or payload.get("key") != key):
        raise LocalHealError("act2 cleanup response does not prove the requested exact key")
    count, size = payload.get("deleted_count"), payload.get("reclaimed_archive_bytes")
    if type(count) is not int or not 0 <= count <= 100_000 or type(size) is not int or not 0 <= size <= 2**63 - 1:
        raise LocalHealError("act2 cleanup response has invalid deletion accounting")
    return LocalHealReceipt(1, key, count, size)


def heal_local(base: str, key: str, *, ref: str | None = None, dry_run: bool = False) -> LocalHealReceipt:
    if ref is not None:
        raise LocalHealError("act2 cache namespaces do not support GitHub ref filtering")
    if not key or len(key.encode("utf-8")) > 512 or key.strip() != key or any(ord(c) < 32 or ord(c) == 127 for c in key):
        raise LocalHealError("invalid exact cache key")
    url = _endpoint(base, key)
    if dry_run:
        return LocalHealReceipt(1, key, 0, 0, True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(url, method="DELETE")
    try:
        with opener.open(request, timeout=15) as response:  # noqa: S310 -- validated local literal IP and token path
            if response.status != 200:
                raise LocalHealError("act2 cleanup returned an unsupported status")
            raw = response.read(65_537)
        if len(raw) > 65_536:
            raise LocalHealError("act2 cleanup response exceeds its size bound")
        payload: JsonValue = json.loads(raw, object_pairs_hook=unique_json_object)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise LocalHealError("act2 exact-key cleanup failed; prior state must be inspected before claiming deletion") from exc
    return _receipt(payload, key)
