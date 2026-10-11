"""Synthetic boundary tests; real market export is checked separately."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tw_stock_screener.results import SQLiteResultStore
from tw_stock_screener.web_export import REPLAY_CHECKS, export_site, make_public_report, read_version


def fixture():
    row = {"rank": 1, "market": "TWSE", "code": "2330", "name": "合成測試",
           "change_pct": 5.0, "volume": 10000, "volume_ratio": 4.0,
           "daily_k": 70.0, "daily_d": 60.0, "weekly_k": 70.0, "weekly_d": 60.0,
           "monthly_k": 70.0, "monthly_d": 60.0}
    report = {"status": "complete", "as_of": "2026-10-08", "previous_session": "2026-10-07",
              "generated_at": "2026-10-11T08:00:00+00:00", "job": {"id": "test"},
              "results": [row], "candidates": [copy.deepcopy(row)], "errors": [],
              "counts": {"top_n": 1}, "config": {"selection": {"top_n": 1, "volume_ratio": 3.2},
                  "kd": {"period": 26, "k_smoothing": 3, "d_smoothing": 3, "initial": 50.0, "warmup_periods": 60},
                  "history": {"months": 120, "daily_months": 6, "weekly_months": 24},
                  "paths": {"cache": "C:/private/secret.sqlite3"}},
              "recovery": {"original_report": "D:/private/secret.json"}}
    record = {"version": "test-version", "sha256": "test-hash", "official": False, "report": report}
    summary = {"version": "test-version", "job_id": "test", "as_of": report["as_of"], "status": "complete",
               "counts": report["counts"], "outcomes": {"processed": 1, "selected": 1, "data_insufficient": 0,
                   "condition_rejected": 0, "ranking_errors": 0},
               "verification": {"matched": True, "checks": {k: True for k in REPLAY_CHECKS}}}
    return record, summary


class WebExportTests(unittest.TestCase):
    def test_private_fields_and_exception_paths_are_not_published(self):
        record, summary = fixture()
        record["report"]["errors"].append({"stage": "ranking", "market": "TPEX", "code": "1234",
                                             "reason": "Failed C:/private/access-token"})
        summary["outcomes"]["ranking_errors"] = 1
        result = make_public_report(record, summary)
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn("paths", result["rules"])
        self.assertEqual(result["publication_status"], "pending")
        self.assertEqual(result["candidates"][0]["daily_k"], 70.0)

    def test_incomplete_sample_or_wrong_summary_is_rejected(self):
        record, summary = fixture()
        for mutate in (lambda r, s: r["report"].update(report_scope="validation_sample"),
                       lambda r, s: s.update(version="another"),
                       lambda r, s: s["verification"]["checks"].pop("input_hashes"),
                       lambda r, s: s["verification"]["checks"].update(no_http_during_replay=False),
                       lambda r, s: s["outcomes"].update(selected=9)):
            r, s = copy.deepcopy(record), copy.deepcopy(summary)
            mutate(r, s)
            with self.assertRaises(ValueError):
                make_public_report(r, s)

    def test_official_status_is_read_only_and_content_hash_checked(self):
        record, summary = fixture()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "results.sqlite3"
            with SQLiteResultStore(path) as store:
                version = store.add(record["report"])
                self.assertFalse(read_version(path, version)["official"])
                store.approve(version, None)
                self.assertTrue(read_version(path, version)["official"])
                store.db.execute("UPDATE versions SET sha256='bad' WHERE id=?", (version,))
                store.db.commit()
                with self.assertRaises(ValueError):
                    read_version(path, version)

    def test_export_escapes_code_and_keeps_previous_version(self):
        record, summary = fixture()
        record["report"]["candidates"][0]["name"] = '</script><script>alert("test")</script>'
        assets = Path(__file__).resolve().parents[1] / "web"
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "site"
            result = export_site(record, summary, assets, output)
            self.assertEqual(result["candidates"][0]["name"], record["report"]["candidates"][0]["name"])
            self.assertNotIn("</script>", (output / "data.js").read_text(encoding="utf-8"))
            self.assertTrue((output / "selected.csv").read_bytes().startswith(b"\xef\xbb\xbf"))
            snapshot = hashlib.sha256((output / "data.json").read_bytes()).hexdigest()
            record["version"] = summary["version"] = "new-version"
            with self.assertRaises(ValueError):
                export_site(record, summary, assets, output)
            self.assertEqual(snapshot, hashlib.sha256((output / "data.json").read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
