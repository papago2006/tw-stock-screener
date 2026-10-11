"""Recovery transport/cutoff tests; byte payloads here are test-only."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import gzip
import hashlib
import json
import unittest

from tools.recover_run import ArchiveFirstSource, RecoveryCache
from tw_stock_screener.models import DataError


class ResumeTests(unittest.TestCase):
    def archive(self, root, content, wrong_hash=False):
        digest = hashlib.sha256(content).hexdigest()
        (root / f"{digest}.gz").write_bytes(gzip.compress(b'corrupt' if wrong_hash else content))
        (root / "requests.jsonl").write_text(json.dumps({"url": "https://www.twse.com.tw/test",
                                                       "sha256": digest}) + "\n", encoding="utf-8")
        return ArchiveFirstSource(dict(timeout=1, attempts=1, interval_seconds=0), root)

    def test_verified_raw_is_reused_without_http(self):
        with TemporaryDirectory() as temp:
            body = b'{"stat":"OK","date":"20261008"}'
            source = self.archive(Path(temp), body)
            with patch("tw_stock_screener.sources.OfficialSource.fetch", side_effect=AssertionError("HTTP forbidden")):
                self.assertEqual(source.fetch("https://www.twse.com.tw/test"), body)
            self.assertEqual(source.archive_hits, 1)
            self.assertEqual(source.network_calls, 0)

    def test_bad_hash_and_cached_api_failure_require_real_refetch(self):
        for body, bad_hash in [(b'{"stat":"ok"}', True), (b'{"stat":"maintenance"}', False)]:
            with TemporaryDirectory() as temp:
                source = self.archive(Path(temp), body, bad_hash)
                with patch("tw_stock_screener.sources.OfficialSource.fetch", return_value=b"new official test response") as http:
                    self.assertEqual(source.fetch("https://www.twse.com.tw/test"), b"new official test response")
                self.assertEqual(http.call_count, 1)
                self.assertEqual(source.archive_hits, 0)

    def test_unknown_post_cutoff_session_cannot_fetch_or_be_invented(self):
        with TemporaryDirectory() as temp:
            cache = RecoveryCache(Path(temp) / "db.sqlite3", date(2026, 10, 8))
            source = Mock()
            try:
                with self.assertRaises(DataError):
                    cache.ensure(source, "TWSE", date(2026, 10, 9))
                source.daily.assert_not_called()
                self.assertIsNone(cache.status("TWSE", date(2026, 10, 9)))
            finally:
                cache.close()

    def test_original_closed_session_is_reused_without_later_price_request(self):
        with TemporaryDirectory() as temp:
            cache = RecoveryCache(Path(temp) / "db.sqlite3", date(2026, 10, 8))
            source = Mock()
            try:
                with cache.db:
                    cache.db.execute("INSERT INTO days VALUES (?,?,?)", ("TWSE", "2026-10-09", "closed"))
                cache.ensure(source, "TWSE", date(2026, 10, 9))
                source.daily.assert_not_called()
                with self.assertRaises(DataError):
                    cache.ensure(source, "TWSE", date(2026, 10, 9), refresh=True)
            finally:
                cache.close()


if __name__ == "__main__":
    unittest.main()
