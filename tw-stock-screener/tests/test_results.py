"""Synthetic reports here exercise version workflow, never real market signals."""
from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tw_stock_screener.cli import main
from tw_stock_screener.models import DataError
from tw_stock_screener.results import SQLiteResultStore
from tw_stock_screener.tracking import track_official


def report(day="2025-09-30", previous="2025-09-26", codes=("2330",), k=60., errors=None):
    return {"status": "complete", "as_of": day, "previous_session": previous,
            "provenance": {"rules_sha256": "same-test-rules"}, "errors": errors or [],
            "results": [{"market": "TWSE", "code": c, "name": "synthetic",
                         "daily_k": k, "daily_d": 50., "volume_ratio": 3.2} for c in codes]}


class ResultTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "results.sqlite3"
        self.store = SQLiteResultStore(self.path)

    def tearDown(self):
        self.store.__exit__()
        self.temp.cleanup()

    def approve(self, doc, expected=None, allow_errors=False):
        version = self.store.add(doc)
        self.store.approve(version, expected, allow_errors)
        return version

    def test_recalculation_keeps_old_official_and_report_immutable(self):
        original = report()
        first = self.approve(original)
        second = self.store.add(report(k=70))
        original["results"][0]["daily_k"] = 999
        self.assertEqual(self.store.official("2025-09-30")["version"], first)
        self.assertEqual(self.store.get(first)["report"]["results"][0]["daily_k"], 60)
        self.assertFalse(self.store.get(second)["official"])
        self.assertEqual(len(self.store.versions("2025-09-30")), 2)

    def test_approval_replaces_pointer_preserving_old_version_and_audit(self):
        first = self.approve(report())
        second = self.approve(report(k=70), first)
        self.assertFalse(self.store.get(first)["official"])
        self.assertTrue(self.store.get(second)["official"])
        rows = self.store.db.execute("SELECT previous_version FROM approvals ORDER BY id").fetchall()
        self.assertEqual([r[0] for r in rows], [None, first])

    def test_stale_approval_from_second_connection_cannot_overwrite(self):
        first = self.approve(report())
        second = self.approve(report(k=70), first)
        pending = self.store.add(report(k=80))
        with SQLiteResultStore(self.path) as other:
            with self.assertRaisesRegex(DataError, "changed"):
                other.approve(pending, first)
        self.assertEqual(self.store.official("2025-09-30")["version"], second)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM approvals").fetchone()[0], 2)

    def test_error_report_requires_acknowledgement_and_failed_run_not_saved(self):
        doc = report(errors=[{"reason": "missing price"}])
        version = self.store.add(doc)
        with self.assertRaisesRegex(DataError, "errors"):
            self.store.approve(version, None)
        self.assertIsNone(self.store.official(doc["as_of"]))
        self.store.approve(version, None, allow_errors=True)
        failed = deepcopy(doc)
        failed["status"] = "failed"
        with self.assertRaises(DataError):
            self.store.add(failed)

    def test_duplicate_stock_and_invalid_session_are_rejected(self):
        for doc in [report(codes=("2330", "2330")), report(previous="2025-09-30")]:
            with self.assertRaises(DataError):
                self.store.add(doc)

    def test_integrity_check_detects_changed_payload(self):
        version = self.store.add(report())
        with self.store.db:
            self.store.db.execute("UPDATE versions SET report='{}' WHERE id=?", (version,))
        with self.assertRaisesRegex(DataError, "integrity"):
            self.store.get(version)

    def test_empty_official_is_valid_selection_and_repeat_approval_is_idempotent(self):
        version = self.approve(report(codes=()))
        self.store.approve(version, version)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM approvals").fetchone()[0], 1)

    def test_tracking_exact_previous_session_and_metric_delta(self):
        self.approve(report("2025-09-26", "2025-09-25", ("2330", "2317")))
        self.approve(report(codes=("2330", "2454"), k=65))
        result = track_official(self.store, "2025-09-30")
        self.assertEqual(result["status"], "complete")
        self.assertEqual([r["code"] for r in result["new"]], ["2454"])
        self.assertEqual([r["code"] for r in result["continued"]], ["2330"])
        self.assertEqual([r["code"] for r in result["exited"]], ["2317"])
        self.assertEqual(result["changes"][0]["metrics"]["daily_k"]["delta"], 5)
        self.assertEqual(result["streaks"][0], {"market": "TWSE", "code": "2330", "sessions": 2, "exact": False})

    def test_missing_official_does_not_skip_to_older_day_or_report_new(self):
        self.approve(report("2025-09-25", "2025-09-24"))
        self.approve(report())
        result = track_official(self.store, "2025-09-30")
        self.assertEqual(result["status"], "insufficient_history")
        self.assertIsNone(result["new"])

    def test_streak_exact_only_after_known_absence(self):
        self.approve(report("2025-09-25", "2025-09-24", ()))
        self.approve(report("2025-09-26", "2025-09-25"))
        self.approve(report())
        result = track_official(self.store, "2025-09-30")
        self.assertEqual(result["streaks"][0]["sessions"], 2)
        self.assertTrue(result["streaks"][0]["exact"])

    def test_revision_of_previous_day_immediately_changes_tracking(self):
        first = self.approve(report("2025-09-26", "2025-09-25", ()))
        self.approve(report())
        self.assertEqual(len(track_official(self.store, "2025-09-30")["new"]), 1)
        self.approve(report("2025-09-26", "2025-09-25"), first)
        self.assertEqual(len(track_official(self.store, "2025-09-30")["continued"]), 1)

    def test_acknowledged_errors_cannot_create_false_exit_tracking(self):
        self.approve(report("2025-09-26", "2025-09-25"))
        self.approve(report(codes=(), errors=[{"reason": "API failed"}]), allow_errors=True)
        value = track_official(self.store, "2025-09-30")
        self.assertEqual(value["status"], "incomplete_data")
        self.assertIsNone(value["exited"])

    def test_changed_rules_are_not_presented_as_normal_tracking(self):
        self.approve(report("2025-09-26", "2025-09-25"))
        doc = report()
        doc["provenance"]["rules_sha256"] = "different-rules"
        self.approve(doc)
        self.assertEqual(track_official(self.store, "2025-09-30")["status"], "incompatible_rules")

    def test_result_commands_work_without_creating_market_source_or_cache(self):
        cfg = Path(self.temp.name) / "config.yaml"
        cfg.write_text("paths:\n  results: results.sqlite3\n  output: out\n", encoding="utf-8")
        version = self.store.add(report())
        common = ["--config", str(cfg)]
        with patch("tw_stock_screener.cli.OfficialSource", side_effect=AssertionError("no network")), \
             patch("tw_stock_screener.cli.Cache", side_effect=AssertionError("no market DB")), \
             redirect_stdout(StringIO()) as out:
            self.assertEqual(main(common + ["versions"]), 0)
            self.assertIn(version, out.getvalue())
            self.assertEqual(main(common + ["show-result", "--version", version]), 0)
            self.assertEqual(main(common + ["approve", "--version", version, "--expected-current", "none"]), 0)
            self.assertEqual(main(common + ["track", "--date", "2025-09-30"]), 0)


if __name__ == "__main__":
    unittest.main()
