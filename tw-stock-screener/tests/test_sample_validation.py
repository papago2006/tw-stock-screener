"""Synthetic unit tests for sample boundaries and local write failures."""
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import json
import logging
import os
import unittest

from tw_stock_screener.config import DEFAULT
from tw_stock_screener.models import DataError, Quote, Security
from tw_stock_screener.output import export_report, write_json
from tw_stock_screener.results import SQLiteResultStore
from tw_stock_screener.screener import screen
from tools.recover_run import Progress


class SampleTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2025, 9, 30)
        self.securities = [Security("TWSE", str(1000+i), "synthetic", "ESVUFR", date(2000, 1, 1))
                           for i in range(301)]
        self.quotes = [Quote(self.day, "TWSE", s.code, s.name, 10., 12., 8., 11., 100, 1.)
                       for s in self.securities]

    def run_sample(self, limit=30):
        loader = Mock(side_effect=DataError("synthetic missing history"))
        report = screen(self.day, self.quotes, self.securities,
                        [self.day-timedelta(days=1), self.day], DEFAULT,
                        stage_loader=loader, evaluation_limit=limit)
        return report, loader

    def test_first_30_only_keeps_original_top_300_ranking(self):
        report, loader = self.run_sample()
        self.assertEqual(report["counts"]["top_n"], 300)
        self.assertEqual(len(report["validation"]["ranking"]), 300)
        self.assertEqual(report["validation"]["unevaluated"], 270)
        self.assertEqual([r["code"] for r in report["candidates"]], [str(1000+i) for i in range(30)])
        self.assertEqual([c.args[1] for c in loader.call_args_list], [str(1000+i) for i in range(30)])
        self.assertEqual(len(report["errors"]), 30)
        self.assertFalse(report["results"])

    def test_invalid_limits_rejected(self):
        for value in [0, -1, 301, True, 2.5]:
            with self.subTest(value=value), self.assertRaises(DataError):
                self.run_sample(value)

    def test_normal_screen_still_evaluates_300(self):
        report, loader = self.run_sample(None)
        self.assertEqual(loader.call_count, 300)
        self.assertEqual(len(report["candidates"]), 300)
        self.assertNotIn("report_scope", report)

    def test_sample_cannot_be_saved_as_daily_version(self):
        report, _ = self.run_sample()
        report["status"] = "complete"
        with TemporaryDirectory() as temp, SQLiteResultStore(Path(temp)/"results.db") as store:
            with self.assertRaisesRegex(DataError, "Validation samples"):
                store.add(report)
            self.assertEqual(store.versions(), [])

    def test_export_names_explicitly_mark_validation_sample(self):
        report, _ = self.run_sample()
        with TemporaryDirectory() as temp:
            jp, cp = export_report(report, temp)
            self.assertTrue(jp.name.startswith("validation-30-"))
            self.assertTrue(cp.is_file())
            self.assertEqual(json.loads(jp.read_text(encoding="utf-8"))["validation"]["evaluated"], 30)


class WriteFailureTests(unittest.TestCase):
    def test_transient_replace_lock_retries_then_succeeds(self):
        real_replace = os.replace
        calls = []
        def locked_once(src, dest):
            calls.append(dest)
            if len(calls) == 1:
                raise PermissionError("test temporary lock")
            return real_replace(src, dest)
        with TemporaryDirectory() as temp:
            path = Path(temp)/"state.json"
            write_json(path, {"old": True})
            with patch("tw_stock_screener.output.os.replace", side_effect=locked_once), \
                    patch("tw_stock_screener.output.time.sleep"):
                write_json(path, {"new": True})
            self.assertEqual(len(calls), 2)
            self.assertEqual(json.loads(path.read_text()), {"new": True})
            self.assertEqual(list(Path(temp).glob("*.tmp")), [])

    def test_persistent_critical_write_failure_raises_and_preserves_old_file(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)/"state.json"
            write_json(path, {"old": True})
            with patch("tw_stock_screener.output.os.replace", side_effect=PermissionError("locked")) as replace, \
                    patch("tw_stock_screener.output.time.sleep"), self.assertRaises(PermissionError):
                write_json(path, {"new": True})
            self.assertEqual(replace.call_count, 4)
            self.assertEqual(json.loads(path.read_text()), {"old": True})
            self.assertEqual(list(Path(temp).glob("*.tmp")), [])

    def test_progress_write_failure_does_not_abort_calculation_or_recurse(self):
        handler = Progress(Path("unused-test-state.json"), Mock(archive_hits=1, network_calls=2), {})
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            with patch("tools.recover_run.write_json", side_effect=PermissionError("locked")) as save, \
                    self.assertLogs(level="WARNING") as captured:
                record = logging.LogRecord("test", logging.INFO, "", 0,
                                           "candidate rank=12/30 market=TPEX code=6510", (), None)
                handler.emit(record)
            self.assertEqual(save.call_count, 1)
            self.assertEqual(handler.state["progress_write_errors"], 1)
            self.assertEqual(handler.state["candidate"], record.getMessage())
            self.assertIn("progress_write_failed", captured.output[0])
        finally:
            root.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
