"""Raw-evidence tests use explicitly altered test-only official-format fixtures."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import gzip
import hashlib
import json
import unittest

from tw_stock_screener.diagnostics import RawEvidence
from tw_stock_screener.sources import parse_monthly


class EvidenceTests(unittest.TestCase):
    def archive(self, root, bad_hash=False, host="www.tpex.org.tw"):
        doc = json.loads((Path(__file__).parent/"fixtures/tpex_month.json").read_text(encoding="utf-8"))
        table = doc["tables"][0]
        table["data"][0][table["fields"].index("開盤")] = "--"
        content = json.dumps(doc, ensure_ascii=False).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        (root/f"{digest}.gz").write_bytes(gzip.compress(b"test corruption" if bad_hash else content))
        url = f"https://{host}/www/zh-tw/afterTrading/tradingStock?response=json&date=2025%2F09%2F01&code=6488"
        (root/"requests.jsonl").write_text(json.dumps({"url": url, "sha256": digest})+"\n", encoding="utf-8")
        rows = parse_monthly("TPEX", "6488", date(2025, 9, 1), date(2025, 9, 30), doc)
        return RawEvidence(root), rows

    def test_saved_null_price_is_verified_without_inventing_a_suspension(self):
        with TemporaryDirectory() as temp:
            source, rows = self.archive(Path(temp))
            row = rows[0]
            evidence = source.check("TPEX", "6488", row.date, date(2025, 9, 30),
                                    prices=[row.open, row.high, row.low, row.close])
            self.assertTrue(evidence["verified"])
            self.assertEqual(evidence["finding"], "official_response_confirms_cached_prices")
            self.assertNotIn("suspension", evidence)

    def test_hash_mismatch_cannot_be_official_evidence(self):
        with TemporaryDirectory() as temp:
            source, rows = self.archive(Path(temp), bad_hash=True)
            row = rows[0]
            evidence = source.check("TPEX", "6488", row.date, date(2025, 9, 30),
                                    prices=[row.open, row.high, row.low, row.close])
            self.assertFalse(evidence["verified"])

    def test_unofficial_host_cannot_be_promoted_by_a_matching_url_path(self):
        with TemporaryDirectory() as temp:
            source, rows = self.archive(Path(temp), host="example.test")
            row = rows[0]
            evidence = source.check("TPEX", "6488", row.date, date(2025, 9, 30),
                                    prices=[row.open, row.high, row.low, row.close])
            self.assertFalse(evidence["verified"])


if __name__ == "__main__":
    unittest.main()
