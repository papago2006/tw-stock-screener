"""Managed workflow tests. All generated prices/classifications are synthetic."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from tw_stock_screener.application import ScreenRequest
from tw_stock_screener.cache import Cache
from tw_stock_screener.config import DEFAULT
from tw_stock_screener.demand import DemandHistory, CheckedDemandHistory
from tw_stock_screener.jobs import JobLock, WorkflowSource, read_status
from tw_stock_screener.models import DataError, Security
from tw_stock_screener.output import write_json
from tw_stock_screener.results import SQLiteResultStore
from tw_stock_screener.workflow import run_workflow
from test_core import quote, test_history
from test_demand import FakeSource


class CheckedHistoryTests(unittest.TestCase):
    def test_known_invalid_recent_month_stops_before_downloading_older_months(self):
        with TemporaryDirectory() as temp:
            cache = Cache(Path(temp)/"cache.db")
            source = FakeSource()
            securities = [Security("TWSE", "2330", "synthetic", "ESVUFR", date(2000, 1, 1))]
            provider = CheckedDemandHistory(cache, source, date(2025, 9, 15), securities, DEFAULT)
            rows = source.monthly("TWSE", "2330", date(2025, 9, 1), provider.as_of)
            rows[0] = replace(rows[0], open=None)
            with cache.db:
                cache.db.execute("INSERT INTO stock_months VALUES (?,?,?,?,?)", ("TWSE", "2330", "2025-09-01", "2025-09-15", json.dumps([q.record() for q in rows])))
            source.history_calls.clear()
            try:
                with self.assertRaisesRegex(DataError, "Invalid/missing"):
                    provider.stage("TWSE", "2330", "weekly")
                self.assertEqual(source.history_calls, [])
            finally:
                cache.close()

    def test_incomplete_coverage_is_refetched_instead_of_called_missing(self):
        with TemporaryDirectory() as temp:
            cache = Cache(Path(temp)/"cache.db")
            source = FakeSource()
            securities = [Security("TWSE", "2330", "synthetic", "ESVUFR", date(2000, 1, 1))]
            provider = CheckedDemandHistory(cache, source, date(2025, 9, 15), securities, DEFAULT)
            with cache.db:
                cache.db.execute("INSERT INTO stock_months VALUES (?,?,?,?,?)", ("TWSE", "2330", "2025-09-01", "2025-09-10", "[]"))
            try:
                rows, calendar = provider.stage("TWSE", "2330", "daily")
                self.assertEqual({q.date for q in rows}, set(calendar))
                self.assertIn(date(2025, 9, 1), [c[2] for c in source.history_calls])
            finally:
                cache.close()


class JobBoundaryTests(unittest.TestCase):
    def test_duplicate_lock_fails_and_released_lock_can_be_reacquired(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)/"work.lock"
            with JobLock(path):
                with self.assertRaises(DataError):
                    with JobLock(path):
                        self.fail("duplicate acquired lock")
            with JobLock(path):
                self.assertTrue(path.is_file())

    def test_exhausted_failure_survives_restart_without_another_http_budget(self):
        with TemporaryDirectory() as temp:
            network = dict(timeout=1, attempts=3, interval_seconds=0)
            path = Path(temp)/"failures.json"
            source = WorkflowSource(network, Path(temp)/"raw", path)
            reason = "TWSE 2330 2025-09-01: Official API failed: synthetic timeout"
            with patch("tw_stock_screener.recovery.ArchiveFirstSource.monthly", side_effect=DataError(reason)) as fetch:
                for _ in range(2):
                    with self.assertRaisesRegex(DataError, "synthetic timeout"):
                        source.monthly("TWSE", "2330", date(2025, 9, 1), date(2025, 9, 30))
                self.assertEqual(fetch.call_count, 1)
            other = WorkflowSource(network, Path(temp)/"raw", path)
            self.assertEqual(other.history_failure("TWSE", "2330", date(2025, 9, 1)), reason)
            other.replay_only = True
            with self.assertRaises(RuntimeError):
                other.fetch("https://www.twse.com.tw/test")

    def test_schema_ambiguity_is_fatal_and_not_memoized_as_insufficient_data(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)/"failures.json"
            source = WorkflowSource(dict(timeout=1, attempts=1, interval_seconds=0), Path(temp)/"raw", path)
            with patch("tw_stock_screener.recovery.ArchiveFirstSource.monthly", side_effect=DataError("Changed monthly historical schema")):
                with self.assertRaises(RuntimeError):
                    source.monthly("TWSE", "2330", date(2025, 9, 1), date(2025, 9, 30))
            self.assertFalse(path.exists())

    def test_status_detects_interruption_without_starting_a_worker(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            lock = root/"work.lock"
            write_json(root/"pointer.json", {"job_directory": str(root)})
            write_json(root/"run-state.json", {"status": "running", "lock_path": str(lock)})
            with JobLock(lock):
                self.assertEqual(read_status(root/"pointer.json")["status"], "running")
            self.assertEqual(read_status(root/"pointer.json")["status"], "interrupted")


class ManagedIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.days, cls.bars = test_history()
        cls.as_of = cls.days[-1]

    def setup_inputs(self, root):
        cfg = deepcopy(DEFAULT)
        cfg["selection"]["top_n"] = 1
        cfg["paths"] = {"cache": str(root/"cache.db"), "raw": str(root/"raw"),
                        "output": str(root/"out"), "results": str(root/"results.db")}
        cache = Cache(cfg["paths"]["cache"])
        for day, row in [(self.days[-2], self.bars[-2]), (self.as_of, self.bars[-1])]:
            cache.save("TWSE", day, [row])
            cache.save("TPEX", day, [replace(row, market="TPEX", code="6488", change=0.)])
        cache.close()
        universe = root/"universe.json"
        write_json(universe, {"as_of": str(self.as_of), "source_urls": ["https://isin.twse.com.tw/isin/C_public.jsp"],
            "securities": [{"market": m, "code": c, "name": "synthetic", "cfi": "ESVUFR", "listed_on": "2010-01-01"}
                           for m,c in [("TWSE", "2330"), ("TPEX", "6488")]]})
        return cfg, ScreenRequest(date=self.as_of, universe=str(universe), offline=True)

    def test_automatic_sample_to_full_replay_pending_and_idempotent_resume(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            cfg, request = self.setup_inputs(root)
            with patch.object(DemandHistory, "calendar", return_value=self.days), \
                 patch.object(CheckedDemandHistory, "stage", return_value=(self.bars, self.days)), \
                 patch.object(WorkflowSource, "fetch", side_effect=AssertionError("HTTP forbidden")):
                summary = run_workflow(cfg, request, self.as_of)
                repeated = run_workflow(cfg, request, self.as_of)
            self.assertEqual(summary, repeated)
            self.assertEqual(summary["status"], "complete")
            self.assertEqual(summary["outcomes"]["selected"], 1)
            self.assertTrue(summary["verification"]["matched"])
            with SQLiteResultStore(cfg["paths"]["results"]) as store:
                self.assertEqual(len(store.versions()), 1)
                self.assertIsNone(store.official(str(self.as_of)))
            job = Path(summary["job_directory"])
            self.assertTrue((job/"sample-diagnostics.json").exists())
            self.assertIn("尚非正式結果", (job/"report.md").read_text(encoding="utf-8"))

    def test_per_stock_missing_data_finishes_with_exclusions_and_never_passes(self):
        with TemporaryDirectory() as temp:
            cfg, request = self.setup_inputs(Path(temp))
            with patch.object(DemandHistory, "calendar", return_value=self.days), \
                 patch.object(CheckedDemandHistory, "stage", side_effect=DataError("synthetic missing prices")), \
                 patch.object(WorkflowSource, "fetch", side_effect=AssertionError("HTTP forbidden")):
                summary = run_workflow(cfg, request, self.as_of)
            self.assertEqual(summary["status"], "complete_with_exclusions")
            self.assertEqual(summary["outcomes"]["selected"], 0)
            self.assertEqual(summary["outcomes"]["data_insufficient"], 1)
            self.assertTrue(summary["verification"]["matched"])

    def test_changed_result_storage_is_not_mistaken_for_an_already_completed_job(self):
        with TemporaryDirectory() as temp:
            cfg, request = self.setup_inputs(Path(temp))
            with patch.object(DemandHistory, "calendar", return_value=self.days), \
                 patch.object(CheckedDemandHistory, "stage", return_value=(self.bars, self.days)):
                first = run_workflow(cfg, request, self.as_of)
                cfg["paths"]["results"] = str(Path(temp)/"different-results.db")
                second = run_workflow(cfg, request, self.as_of)
            self.assertNotEqual(first["job_id"], second["job_id"])
            with SQLiteResultStore(cfg["paths"]["results"]) as store:
                self.assertEqual(len(store.versions()), 1)

    def test_sample_replay_mismatch_stops_before_full_and_before_version_creation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            cfg, request = self.setup_inputs(root)
            calls = [(self.bars, self.days)]*3+[DataError("synthetic replay differs")]
            with patch.object(DemandHistory, "calendar", return_value=self.days), \
                 patch.object(CheckedDemandHistory, "stage", side_effect=calls) as stage:
                with self.assertRaisesRegex(DataError, "離線重算不一致"):
                    run_workflow(cfg, request, self.as_of)
            self.assertEqual(stage.call_count, 4)
            self.assertFalse(Path(cfg["paths"]["results"]).exists())
            job = next((root/"out/jobs").iterdir())
            self.assertFalse((job/"calculation.json").exists())
            self.assertTrue(list(job.glob("failure-*.json")))


if __name__ == "__main__":
    unittest.main()
