"""Regressions from the real overnight run; fabricated reports are test-only."""
from copy import deepcopy
from datetime import date
from io import BytesIO, TextIOWrapper
from http.client import IncompleteRead
from pathlib import Path
from tempfile import TemporaryDirectory
import gzip
import hashlib
import json
import unittest
from unittest.mock import patch

from tw_stock_screener.cli import main
from tw_stock_screener.models import DataError
from tw_stock_screener.results import SQLiteResultStore
from tw_stock_screener.sources import OfficialSource, parse_monthly, raw_response_path


FIXTURES = Path(__file__).parent / "fixtures"


class HistoricalFormatTests(unittest.TestCase):
    def test_original_old_tpex_units_preserve_ohlc_and_unknown_exact_volume(self):
        doc = json.loads((FIXTURES / "tpex_1799_202410.json").read_text(encoding="utf-8"))
        rows = parse_monthly("TPEX", "1799", date(2024, 10, 1), date(2024, 10, 4), doc)
        self.assertEqual([q.date for q in rows], [date(2024, 10, 1), date(2024, 10, 4)])
        self.assertEqual((rows[0].open, rows[0].high, rows[0].low, rows[0].close),
                         (100., 100., 97.2, 97.2))
        self.assertTrue(all(q.volume is None for q in rows))

    def test_original_listing_annotation_is_a_date_not_a_missing_session(self):
        doc = json.loads((FIXTURES / "tpex_6971_202510.json").read_text(encoding="utf-8"))
        rows = parse_monthly("TPEX", "6971", date(2025, 10, 1), date(2025, 10, 2), doc)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].date, date(2025, 10, 2))
        self.assertEqual(rows[0].close, 40.)
        self.assertIsNone(rows[0].volume)

    def test_unknown_or_ambiguous_volume_schema_still_fails(self):
        original = json.loads((FIXTURES / "tpex_1799_202410.json").read_text(encoding="utf-8"))
        for unknown in ("unknown", "成交張數"):
            doc = deepcopy(original)
            table = doc["tables"][0]
            if unknown == "unknown":
                table["fields"][1] = unknown
            else:
                table["fields"].append(unknown)
            with self.assertRaises(DataError):
                parse_monthly("TPEX", "1799", date(2024, 10, 1), date(2024, 10, 31), doc)

    def test_marker_requires_official_explanation_and_cannot_hide_bad_dates(self):
        original = json.loads((FIXTURES / "tpex_6971_202510.json").read_text(encoding="utf-8"))
        for value, notes in [("114/10/02*", []), ("114/10/02**", original["tables"][0]["notes"]),
                             ("114/10/32*", original["tables"][0]["notes"])]:
            doc = deepcopy(original)
            doc["tables"][0]["notes"] = notes
            doc["tables"][0]["data"][0][0] = value
            with self.assertRaises(ValueError):
                parse_monthly("TPEX", "6971", date(2025, 10, 1), date(2025, 10, 31), doc)

    def test_original_fixture_hashes_match_raw_archive(self):
        for item in json.loads((FIXTURES / "recovery_provenance.json").read_text(encoding="utf-8")):
            self.assertEqual(hashlib.sha256((FIXTURES / item["file"]).read_bytes()).hexdigest(),
                             item["sha256"])


class ArchiveFailureTests(unittest.TestCase):
    def source(self, directory):
        return OfficialSource(dict(timeout=1, attempts=3, interval_seconds=0), directory)

    def test_incomplete_response_retries_and_archives_only_complete_content(self):
        class InterruptedResponse(BytesIO):
            def read(self, *args):
                raise IncompleteRead(b'{"partial":')

        with TemporaryDirectory() as temp:
            complete = b'{"test-only-complete-response": true}'
            with patch("tw_stock_screener.sources.open_verified", side_effect=[
                    InterruptedResponse(), BytesIO(complete)]) as http, \
                 patch("tw_stock_screener.sources.time.sleep"):
                self.assertEqual(self.source(temp).fetch("https://www.twse.com.tw/test"), complete)
            self.assertEqual(http.call_count, 2)
            files = list(Path(temp).rglob("*.gz"))
            self.assertEqual(len(files), 1)
            self.assertEqual(gzip.decompress(files[0].read_bytes()), complete)
            records = (Path(temp) / "requests.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(records), 1)

    def test_incomplete_response_exhausts_finite_budget_without_archiving(self):
        class InterruptedResponse(BytesIO):
            def read(self, *args):
                raise IncompleteRead(b'{"partial":')

        with TemporaryDirectory() as temp:
            with patch("tw_stock_screener.sources.open_verified", side_effect=[
                    InterruptedResponse() for _ in range(3)]) as http, \
                 patch("tw_stock_screener.sources.time.sleep"):
                with self.assertRaisesRegex(DataError, "Official API failed"):
                    self.source(temp).fetch("https://www.twse.com.tw/test")
            self.assertEqual(http.call_count, 3)
            self.assertFalse(list(Path(temp).rglob("*.gz")))
            self.assertFalse((Path(temp) / "requests.jsonl").exists())

    def test_atomic_archive_keeps_content_hash_and_provenance(self):
        with TemporaryDirectory() as temp:
            content = b'{"official-response-test": true}'
            source = self.source(temp)
            with patch("tw_stock_screener.sources.open_verified", return_value=BytesIO(content)):
                self.assertEqual(source.fetch("https://www.twse.com.tw/test"), content)
            files = list(Path(temp).rglob("*.gz"))
            self.assertEqual(len(files), 1)
            self.assertEqual(gzip.decompress(files[0].read_bytes()), content)
            record = json.loads((Path(temp) / "requests.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(record["sha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual(files[0].stem, record["sha256"])
            self.assertEqual(files[0], Path(temp) / "v2" / record["sha256"][:2] / files[0].name)
            self.assertEqual(Path(temp) / record["raw_path"], files[0])
            self.assertFalse(list(Path(temp).rglob("*.tmp")))

    def test_legacy_archive_is_reused_without_move_or_duplicate(self):
        with TemporaryDirectory() as temp:
            content = b"legacy test fixture only"
            digest = hashlib.sha256(content).hexdigest()
            legacy = Path(temp) / f"{digest}.gz"
            compressed = gzip.compress(content)
            legacy.write_bytes(compressed)
            self.source(temp).archive("https://www.twse.com.tw/test", content)
            self.assertEqual(raw_response_path(temp, digest), legacy)
            self.assertEqual(legacy.read_bytes(), compressed)
            self.assertFalse((Path(temp) / "v2").exists())

    def test_new_archive_temporary_files_never_use_full_legacy_root(self):
        import tempfile
        with TemporaryDirectory() as temp:
            original = tempfile.NamedTemporaryFile
            def forbid_full_directory(*args, **kwargs):
                if Path(kwargs["dir"]) == Path(temp):
                    raise PermissionError("simulated FAT32 flat-directory limit")
                return original(*args, **kwargs)
            with patch("tw_stock_screener.sources.tempfile.NamedTemporaryFile", side_effect=forbid_full_directory):
                self.source(temp).archive("https://www.twse.com.tw/test", b"sharded test fixture only")
            self.assertEqual(len(list(Path(temp).rglob("*.gz"))), 1)

    def test_raw_path_rejects_invalid_hashes(self):
        for digest in ["../escape", "", "a" * 63]:
            with self.assertRaises(DataError):
                raw_response_path("raw", digest)

    def test_disk_failure_does_not_retry_http_or_leave_partial_archive(self):
        with TemporaryDirectory() as temp:
            with patch("tw_stock_screener.sources.open_verified", return_value=BytesIO(b"test")) as http, \
                 patch("tw_stock_screener.sources.os.replace", side_effect=PermissionError(13, "blocked")):
                with self.assertRaisesRegex(OSError, "Local raw archive failed"):
                    self.source(temp).fetch("https://www.twse.com.tw/test")
            self.assertEqual(http.call_count, 1)
            self.assertFalse(list(Path(temp).rglob("*.gz")))
            self.assertFalse(list(Path(temp).rglob("*.tmp")))
            self.assertFalse((Path(temp) / "requests.jsonl").exists())

    def test_raw_manifest_failure_also_does_not_retry_http(self):
        with TemporaryDirectory() as temp:
            # A directory at this file path deterministically simulates a local
            # manifest write failure without changing Windows permissions.
            (Path(temp) / "requests.jsonl").mkdir()
            with patch("tw_stock_screener.sources.open_verified", return_value=BytesIO(b"test")) as http:
                with self.assertRaisesRegex(OSError, "Local raw archive failed"):
                    self.source(temp).fetch("https://www.twse.com.tw/test")
            self.assertEqual(http.call_count, 1)

    def test_cli_aborts_local_archive_failure_without_pending_or_success_result(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            cfg = root / "config.yaml"
            cfg.write_text("paths:\n  cache: cache.db\n  output: out\n  raw: raw\n  results: results.db\n",
                           encoding="utf-8")
            (root / "raw" / "requests.jsonl").mkdir(parents=True)
            def fetch_only(request, config, cache, source, today):
                return source.fetch("https://www.twse.com.tw/test")
            with patch("tw_stock_screener.cli.run_screen", side_effect=fetch_only), \
                 patch("tw_stock_screener.sources.open_verified", return_value=BytesIO(b"test")) as http:
                self.assertEqual(main(["--config", str(cfg), "screen"]), 2)
            self.assertEqual(http.call_count, 1)
            self.assertFalse(list((root / "out").glob("screen-*.json")))
            self.assertFalse((root / "results.db").exists())
            failures = list((root / "out").glob("failure-*.json"))
            self.assertEqual(len(failures), 1)
            self.assertIn("Local raw archive failed", failures[0].read_text(encoding="utf-8"))


class ConsoleEncodingTests(unittest.TestCase):
    def test_chinese_path_and_result_survive_redirected_cp1252_console(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "中文測試"
            root.mkdir()
            cfg = root / "config.yaml"
            cfg.write_text("paths:\n  cache: cache.db\n  output: 結果\n  raw: raw\n  results: results.db\n",
                           encoding="utf-8")
            # Synthetic report tests CLI encoding/publication only, not selection.
            report = {"status": "complete", "as_of": "2025-09-30", "previous_session": "2025-09-26",
                      "counts": {"volume_ratio": 1}, "errors": [], "results": [
                          {"market": "TWSE", "code": "0000", "name": "測試非真實行情"}]}
            stdout_bytes, stderr_bytes = BytesIO(), BytesIO()
            stdout = TextIOWrapper(stdout_bytes, encoding="cp1252")
            stderr = TextIOWrapper(stderr_bytes, encoding="cp1252")
            with patch("tw_stock_screener.cli.sys.stdout", stdout), \
                 patch("tw_stock_screener.cli.sys.stderr", stderr), \
                 patch("tw_stock_screener.cli.run_screen", return_value=report):
                self.assertEqual(main(["--config", str(cfg), "screen"]), 0)
                stdout.flush()
                summary = json.loads(stdout_bytes.getvalue().decode("utf-8"))
                self.assertIn("中文測試", summary["files"][0])
                self.assertEqual(summary["publication_status"], "pending")
                # Read-only result commands must handle Chinese company names too.
                self.assertEqual(main(["--config", str(cfg), "show-result", "--version", summary["version"]]), 0)
                stdout.flush()
                self.assertIn("測試非真實行情", stdout_bytes.getvalue().decode("utf-8"))
            self.assertFalse(list((root / "結果").glob("failure-*.json")))
            with SQLiteResultStore(root / "results.db") as store:
                self.assertIsNone(store.official("2025-09-30"))


if __name__ == "__main__":
    unittest.main()
