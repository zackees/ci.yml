"""Trusted publisher context keeps event-specific shell logic out of repositories."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from ci_lint.attestation_context import publication_context


class ContextTest(unittest.TestCase):
    def test_pr_uses_head_base_and_positive_number(self) -> None:
        env = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": "/event", "GITHUB_REPOSITORY": "o/r"}
        event = '{"number":7,"repository":{"default_branch":"main"},"pull_request":{"head":{"sha":"' + "a" * 40 + '","repo":{"full_name":"o/r"}},"base":{"sha":"' + "b" * 40 + '"}}}'
        with patch("pathlib.Path.read_text", return_value=event):
            result = publication_context(env)
        self.assertEqual(result.commit, "a" * 40)
        self.assertEqual(result.base, "b" * 40)
        self.assertEqual(result.pr, 7)
        self.assertFalse(result.promote)
        for invalid in (event.replace('"number":7', '"number":true'), event.replace('"number":7', '"number":0'), event.replace("a" * 40, "--option"), event.replace("b" * 40, "bad"), event.replace('"full_name":"o/r"', '"full_name":"fork/r"')):
            with patch("pathlib.Path.read_text", return_value=invalid), self.assertRaises(ValueError):
                publication_context(env)

    def test_only_default_branch_push_promotes(self) -> None:
        env = {"GITHUB_EVENT_NAME": "push", "GITHUB_REPOSITORY": "o/r", "GITHUB_EVENT_PATH": "/event", "GITHUB_SHA": "c" * 40, "GITHUB_REF": "refs/heads/release/1.0"}
        with patch("pathlib.Path.read_text", return_value='{"repository":{"default_branch":"release/1.0"}}'):
            result = publication_context(env)
            self.assertTrue(result.promote)
            self.assertEqual(result.main_ref, "origin/release/1.0")
            for event in ("release", "schedule", "workflow_dispatch"):
                self.assertFalse(publication_context({**env, "GITHUB_EVENT_NAME": event}).enabled)
            self.assertFalse(publication_context({**env, "GITHUB_REF": "refs/tags/v1"}).enabled)

    def test_malformed_payload_and_ids_are_rejected(self) -> None:
        env = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": "/event", "GITHUB_REPOSITORY": "o/r"}
        for body in ('{}', '[]', '{bad', '{"number":true,"repository":{"default_branch":"main"},"pull_request":{}}'):
            with self.subTest(body=body), patch("pathlib.Path.read_text", return_value=body), self.assertRaises(ValueError):
                publication_context(env)


class CommandContextTest(unittest.TestCase):
    def test_release_does_not_publish_or_contact_api(self) -> None:
        import argparse
        import os
        from ci_lint.attest_cli import register

        parser = argparse.ArgumentParser()
        register(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["attest", "keys", "--github-context", "--out-dir", "/unused", "--github-output"])
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "release"}), patch("ci_lint.attest_cli._publish_keys") as publish, patch("ci_lint.attest_cli._write_outputs") as outputs:
            self.assertEqual(args.func(args), 0)
            publish.assert_not_called()
            outputs.assert_called_once_with(["count=0"])
