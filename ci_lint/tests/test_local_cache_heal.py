"""The same heal command chooses the local authority before GitHub credentials."""

import contextlib
import io
import json
import threading
import unittest
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from ci_lint.cli import main


@dataclass(frozen=True)
class CommandResult:
    code: int
    output: str
    error: str


class LocalCacheHealTest(unittest.TestCase):
    def setUp(self):
        remote_guard = patch("ci_lint.cli.default_delete", side_effect=AssertionError("unexpected remote deletion"))
        remote_guard.start()
        self.addCleanup(remote_guard.stop)
        self.calls = []
        self.status = 200
        self.raw = None
        self.payload = {"schema_version": 1, "key": "old-key", "deleted_count": 1,
                        "reclaimed_archive_bytes": 80}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_DELETE(self):
                owner.calls.append(self.path)
                owner.assertIsNone(self.headers.get("Authorization"))
                self.send_response(owner.status)
                if owner.status == 302:
                    self.send_header("Location", "/redirected")
                self.end_headers()
                self.wfile.write(json.dumps(owner.payload).encode() if owner.raw is None else owner.raw)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.shutdown)
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}/" + "a" * 32 + "/"

    def invoke(self, *args, endpoint=None, act="true"):
        env = {"ACT": act, "ACTIONS_CACHE_URL": self.endpoint if endpoint is None else endpoint,
               "GITHUB_TOKEN": "must-not-reach-local-server", "GITHUB_REPOSITORY": "owner/repo"}
        out, err = io.StringIO(), io.StringIO()
        with patch.dict("os.environ", env, clear=True), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["cache", "heal", "--key", "old-key", "--json", *args])
        return CommandResult(code, out.getvalue(), err.getvalue())

    def test_actual_http_heal_uses_local_service_without_github_credentials(self):
        result = self.invoke()
        self.assertEqual(result.code, 0, result.error)
        self.assertEqual(self.calls, ["/" + "a" * 32 + "/_apis/artifactcache/cache?key=old-key"])
        payload = json.loads(result.output)
        self.assertEqual(payload["backend"], "act2")
        self.assertEqual(payload["deleted_count"], 1)
        self.assertEqual(payload["reclaimed_archive_bytes"], 80)
        self.assertNotIn("a" * 32, result.output + result.error)

    def test_local_failures_never_fall_back_to_github_or_follow_redirects(self):
        with patch("ci_lint.cli.default_delete") as remote:
            for endpoint in ("", "https://github.com/" + "a" * 32, "http://127.0.0.1/unsafe",
                             self.endpoint + "?unexpected=query"):
                result = self.invoke(endpoint=endpoint)
                self.assertEqual(result.code, 1)
            self.assertEqual(self.calls, [])
            for status in (302, 404, 503):
                self.status = status
                result = self.invoke()
                self.assertEqual(result.code, 1)
                self.assertNotIn("a" * 32, result.error)
            remote.assert_not_called()
        self.assertFalse(any("redirected" in call for call in self.calls))

    def test_dry_run_and_unsupported_ref_do_not_delete(self):
        result = self.invoke("--dry-run")
        self.assertEqual(result.code, 0, result.error)
        self.assertTrue(json.loads(result.output)["dry_run"])
        self.assertEqual(self.calls, [])
        result = self.invoke("--ref", "refs/heads/main")
        self.assertEqual(result.code, 1)
        self.assertEqual(self.calls, [])

    def test_response_must_prove_exact_key_and_bounded_counts(self):
        for field, value in (("schema_version", 2), ("key", "another-key"), ("deleted_count", -1),
                             ("deleted_count", True), ("deleted_count", 0), ("reclaimed_archive_bytes", -1)):
            with self.subTest(field=field, value=value):
                saved = self.payload[field]
                self.payload[field] = value
                result = self.invoke()
                self.assertEqual(result.code, 1)
                self.payload[field] = saved

    def test_hosted_heal_keeps_github_transport(self):
        with patch("ci_lint.cli.default_delete") as remote:
            result = self.invoke(act="false")
            self.assertEqual(result.code, 0, result.error)
            remote.assert_called_once()
            self.assertTrue(remote.call_args.args[0].startswith("https://api.github.com/repos/owner/repo/actions/caches?"))
        self.assertEqual(self.calls, [])

    def test_absent_exact_key_reports_zero_actual_reclamation(self):
        self.payload["deleted_count"] = 0
        self.payload["reclaimed_archive_bytes"] = 0
        result = self.invoke()
        self.assertEqual(result.code, 0, result.error)
        self.assertEqual(json.loads(result.output)["deleted_count"], 0)

    def test_duplicate_fields_and_oversized_documents_are_rejected(self):
        for raw in (b'{"schema_version":1,"key":"old-key","deleted_count":1,"deleted_count":0,"reclaimed_archive_bytes":0}',
                    b" " * 65_537, b"not JSON"):
            self.raw = raw
            result = self.invoke()
            self.assertEqual(result.code, 1)
            self.assertNotIn("a" * 32, result.error)
