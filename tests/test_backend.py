#!/usr/bin/env python3
"""Проверки backend.py: нормализация, тексты ошибок, вход, журнал, уведомления.

Запуск:  python3 tests/test_backend.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sqlite3
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


def meters(five_hour: dict | None = None, week: dict | None = None, month: dict | None = None) -> dict:
    payload: dict = {"access": {"meters": {}}}
    for name, item in (("fiveHour", five_hour), ("week", week), ("month", month)):
        if item is not None:
            payload["access"]["meters"][name] = item
    return payload


FIXTURE = meters(
    {"usedMicroCents": "126000000", "limitMicroCents": "1000000000", "resetsAt": "2026-01-01T05:00:00.000Z"},
    {"usedMicroCents": "610000000", "limitMicroCents": "1000000000", "resetsAt": "2026-01-07T00:00:00.000Z"},
    {"usedMicroCents": "885000000", "limitMicroCents": "1000000000", "resetsAt": "2026-02-01T00:00:00.000Z"},
)
FIXTURE["product"] = "go"
FIXTURE["unrelated"] = "ignored"


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

    def test_computes_percent_from_micro_cents(self):
        result = backend.normalize(FIXTURE)
        self.assertAlmostEqual(result["rolling"]["percent"], 12.6)
        self.assertAlmostEqual(result["weekly"]["percent"], 61.0)
        self.assertAlmostEqual(result["monthly"]["percent"], 88.5)

    def test_exhausted_window_is_marked_exceeded(self):
        result = backend.normalize(meters(week={"usedMicroCents": "1000", "limitMicroCents": "1000"}))
        self.assertEqual(result["weekly"]["status"], "exceeded")

    def test_active_window_is_not_marked_exceeded(self):
        self.assertEqual(backend.normalize(FIXTURE)["weekly"]["status"], "active")

    def test_missing_resets_at_becomes_empty(self):
        result = backend.normalize(meters(month={"usedMicroCents": "1", "limitMicroCents": "2"}))
        self.assertEqual(result["monthly"]["resetsAt"], "")

    def test_broken_amounts_become_zero(self):
        result = backend.normalize(meters(five_hour={"usedMicroCents": "мусор", "limitMicroCents": None}))
        self.assertEqual(result["rolling"]["percent"], 0.0)
        self.assertEqual(result["rolling"]["status"], "active")

    def test_window_without_meter_is_skipped(self):
        self.assertNotIn("weekly", backend.normalize(meters(five_hour={"usedMicroCents": "1", "limitMicroCents": "2"})))

    def test_broken_payload_raises(self):
        for payload in ({}, {"access": None}, [], "nope", {"access": {"meters": []}}):
            with self.assertRaises(backend.UsageError):
                backend.normalize(payload)

    def test_account_without_go_plan_is_explained(self):
        with self.assertRaises(backend.UsageError) as ctx:
            backend.normalize({"product": "zen"})
        self.assertEqual(str(ctx.exception), backend.MSG_NO_GO)


class ErrorTextTests(unittest.TestCase):
    def test_401(self):
        self.assertIn("401", backend.message_for_http_status(401))

    def test_404(self):
        self.assertIn("404", backend.message_for_http_status(404))

    def test_other_codes(self):
        self.assertIn("500", backend.message_for_http_status(500))


class CredentialsTests(unittest.TestCase):
    """Вход берётся из opencode.db, а не из auth.json."""

    def write_db(self, directory: str, token: str | None = "tok", org: str | None = "wrk_1") -> Path:
        path = Path(directory) / "opencode.db"
        connection = sqlite3.connect(str(path))
        connection.execute("create table account (id text, url text, access_token text, token_expiry integer)")
        connection.execute(
            "insert into account values ('acc_1', 'https://opencode.ai/console', ?, 4102444800000)",
            (token,),
        )
        connection.execute("create table account_state (id integer, active_account_id text, active_org_id text)")
        connection.execute("insert into account_state values (1, 'acc_1', ?)", (org,))
        connection.commit()
        connection.close()
        return path

    def test_missing_database(self):
        with self.assertRaises(backend.UsageError) as ctx:
            backend.load_credentials(Path("/nonexistent/opencode.db"))
        self.assertEqual(str(ctx.exception), backend.MSG_NO_AUTH)

    def test_reads_token_org_and_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            token, org, base, expiry = backend.load_credentials(self.write_db(tmp))
        self.assertEqual(token, "tok")
        self.assertEqual(org, "wrk_1")
        self.assertEqual(base, "https://opencode.ai/console")
        self.assertEqual(expiry, 4102444800000)

    def test_missing_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_db(tmp, token=None)
            with self.assertRaises(backend.UsageError) as ctx:
                backend.load_credentials(path)
        self.assertEqual(str(ctx.exception), backend.MSG_NO_TOKEN)

    def test_missing_org(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_db(tmp, org=None)
            with self.assertRaises(backend.UsageError) as ctx:
                backend.load_credentials(path)
        self.assertEqual(str(ctx.exception), backend.MSG_NO_ORG)

    def test_database_without_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "opencode.db"
            sqlite3.connect(str(path)).close()
            with self.assertRaises(backend.UsageError) as ctx:
                backend.load_credentials(path)
        self.assertIn("opencode.db", str(ctx.exception))


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
        code, document = self.run_main([], backend.UsageError(backend.MSG_NO_TOKEN))
        self.assertEqual(code, 0)
        self.assertFalse(document["ok"])
        self.assertEqual(document["error"], backend.MSG_NO_TOKEN)

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
