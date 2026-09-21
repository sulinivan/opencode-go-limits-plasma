#!/usr/bin/env python3
"""Проверки backend.py: нормализация, тексты ошибок, ключ, журнал, уведомления.

Запуск:  python3 tests/test_backend.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

# Не засоряем пакет виджета кэшем байт-кода.
sys.dont_write_bytecode = True

BACKEND_PATH = Path(__file__).resolve().parent.parent / "package" / "contents" / "code" / "backend.py"


def load_backend():
    spec = importlib.util.spec_from_file_location("limits_backend", BACKEND_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


backend = load_backend()


FIXTURE = {
    "usage": {
        "rolling": {"percent": 12.6, "status": "active", "resetsAt": "2026-01-01T05:00:00Z"},
        "weekly": {"percent": 61, "status": "active", "resetsAt": "2026-01-07T00:00:00Z"},
        "monthly": {"percent": 88.5, "status": "exceeded", "resetsAt": "2026-02-01T00:00:00Z"},
    },
    "unrelated": "ignored",
}


class IsolatedState(unittest.TestCase):
    """Каждый тест пишет журнал и маркер в свой временный каталог."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self._original_state_dir = backend.STATE_DIR
        backend.STATE_DIR = Path(self._temporary.name)

    def tearDown(self):
        backend.STATE_DIR = self._original_state_dir
        self._temporary.cleanup()

    def log_lines(self) -> list[str]:
        log = backend.STATE_DIR / "usage.log"
        if not log.is_file():
            return []
        return log.read_text(encoding="utf-8").splitlines()


class NormalizeTests(unittest.TestCase):
    def test_keeps_only_known_windows(self):
        result = backend.normalize(FIXTURE)
        self.assertEqual(sorted(result), ["monthly", "rolling", "weekly"])
        self.assertNotIn("unrelated", result)

    def test_casts_percent_to_float(self):
        payload = {"usage": {"rolling": {"percent": "42.5", "status": "ok", "resetsAt": "x"}}}
        self.assertEqual(backend.normalize(payload)["rolling"]["percent"], 42.5)

    def test_broken_percent_becomes_zero(self):
        payload = {"usage": {"rolling": {"percent": "мусор", "status": "ok"}}}
        item = backend.normalize(payload)["rolling"]
        self.assertEqual(item["percent"], 0.0)
        self.assertEqual(item["resetsAt"], "")

    def test_window_without_usage_is_skipped(self):
        self.assertNotIn("weekly", backend.normalize({"usage": {"rolling": {}}}))

    def test_broken_payload_raises(self):
        for payload in ({}, {"usage": None}, [], "nope", {"usage": []}):
            with self.assertRaises(backend.UsageError):
                backend.normalize(payload)


class ErrorTextTests(unittest.TestCase):
    def test_401(self):
        self.assertEqual(backend.message_for_http_status(401), "ключ отклонён (401) — проверьте auth.json")

    def test_404(self):
        self.assertIn("404", backend.message_for_http_status(404))

    def test_other_codes(self):
        self.assertIn("500", backend.message_for_http_status(500))


class ApiKeyTests(unittest.TestCase):
    def test_missing_auth_file(self):
        with self.assertRaises(backend.UsageError) as ctx:
            backend.load_api_key(Path("/nonexistent/auth.json"))
        self.assertEqual(str(ctx.exception), backend.MSG_NO_AUTH)

    def write_auth(self, directory: str, document: object) -> Path:
        path = Path(directory) / "auth.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_reads_go_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_auth(tmp, {"opencode-go": {"type": "api", "key": "secret-go"}})
            self.assertEqual(backend.load_api_key(path), "secret-go")

    def test_zen_only_key_is_explained(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_auth(tmp, {"opencode": {"key": "secret-zen"}})
            with self.assertRaises(backend.UsageError) as ctx:
                backend.load_api_key(path)
            self.assertEqual(str(ctx.exception), backend.MSG_ZEN_ONLY)

    def test_no_key_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_auth(tmp, {"something": {"key": "x"}})
            with self.assertRaises(backend.UsageError) as ctx:
                backend.load_api_key(path)
            self.assertEqual(str(ctx.exception), backend.MSG_NO_KEY)

    def test_broken_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "auth.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(backend.UsageError):
                backend.load_api_key(path)


class SharedLogTests(IsolatedState):
    def test_publish_keeps_single_line_and_replaces_it(self):
        backend.publish({"ok": True, "usage": {"rolling": {"percent": 1.0}}})
        backend.publish({"ok": False, "error": "нет связи"})
        lines = self.log_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["error"], "нет связи")

    def test_publish_survives_unwritable_directory(self):
        backend.STATE_DIR = Path("/proc/nonexistent")
        backend.publish({"ok": True})  # не должно бросать исключение


class ClaimTests(IsolatedState):
    def test_window_key_ignores_subsecond_drift(self):
        self.assertEqual(
            backend.window_key("2026-09-21T10:56:48.051Z", 85),
            backend.window_key("2026-09-21T10:56:48.845Z", 85),
        )

    def test_window_key_changes_with_window_and_threshold(self):
        self.assertNotEqual(
            backend.window_key("2026-09-21T10:56:48.051Z", 85),
            backend.window_key("2026-09-21T15:56:00.051Z", 85),
        )
        self.assertNotEqual(
            backend.window_key("2026-09-21T10:56:48.051Z", 85),
            backend.window_key("2026-09-21T10:56:48.051Z", 90),
        )

    def test_claim_is_granted_once_per_key(self):
        self.assertTrue(backend.claim("reset-a|85"))
        self.assertFalse(backend.claim("reset-a|85"))

    def test_new_window_is_claimed_again(self):
        self.assertTrue(backend.claim("reset-a|85"))
        self.assertTrue(backend.claim("reset-b|85"))
        self.assertFalse(backend.claim("reset-b|85"))


class CliTests(IsolatedState):
    def run_main(self, argv, outcome):
        def fake_collect():
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        original = backend.collect
        backend.collect = fake_collect
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                code = backend.main(argv)
        finally:
            backend.collect = original
        return code, json.loads(buffer.getvalue().strip().splitlines()[-1])

    def sample_usage(self, percent: float) -> dict:
        return {"rolling": {"percent": percent, "status": "ok", "resetsAt": "reset-a"}}

    def test_success_is_single_line_json(self):
        code, document = self.run_main([], self.sample_usage(1.0))
        self.assertEqual(code, 0)
        self.assertTrue(document["ok"])
        self.assertEqual(document["usage"]["rolling"]["percent"], 1.0)
        self.assertIn("time", document)

    def test_error_is_reported_as_json_not_traceback(self):
        code, document = self.run_main([], backend.UsageError(backend.MSG_NO_KEY))
        self.assertEqual(code, 0)
        self.assertFalse(document["ok"])
        self.assertEqual(document["error"], backend.MSG_NO_KEY)

    def test_threshold_below_is_not_notified(self):
        _, document = self.run_main(["--threshold", "85"], self.sample_usage(84.9))
        self.assertNotIn("notify", document)

    def test_threshold_crossed_notifies_once_for_all_widgets(self):
        first = self.run_main(["--threshold", "85"], self.sample_usage(90.0))[1]
        second = self.run_main(["--threshold", "85"], self.sample_usage(91.0))[1]
        self.assertTrue(first["notify"])
        self.assertFalse(second["notify"])

    def test_repeated_notification_is_not_caused_by_resets_at_drift(self):
        drifting = {"rolling": {"percent": 90.0, "status": "ok", "resetsAt": "2026-09-21T10:56:48.051Z"}}
        later = {"rolling": {"percent": 90.0, "status": "ok", "resetsAt": "2026-09-21T10:56:49.845Z"}}
        self.assertTrue(self.run_main(["--threshold", "85"], drifting)[1]["notify"])
        self.assertFalse(self.run_main(["--threshold", "85"], later)[1]["notify"])

    def test_new_reset_window_notifies_again(self):
        self.run_main(["--threshold", "85"], {"rolling": {"percent": 90.0, "status": "ok", "resetsAt": "reset-a"}})
        _, document = self.run_main(["--threshold", "85"], {"rolling": {"percent": 90.0, "status": "ok", "resetsAt": "reset-b"}})
        self.assertTrue(document["notify"])

    def test_published_log_matches_printed_line(self):
        _, document = self.run_main([], self.sample_usage(5.0))
        self.assertEqual(json.loads(self.log_lines()[-1]), document)

    def test_test_mode_is_human_readable(self):
        original = backend.collect
        backend.collect = lambda: self.sample_usage(7.0)
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                code = backend.main(["--test"])
        finally:
            backend.collect = original
        self.assertEqual(code, 0)
        self.assertIn("rolling", buffer.getvalue())
        self.assertEqual(self.log_lines(), [])  # --test не трогает общий журнал


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
