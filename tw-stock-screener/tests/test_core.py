"""Synthetic data below is for unit tests only, never a claimed stock-screening result."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import csv
import gzip
import json
import unittest
from unittest.mock import patch
import ssl
from urllib.error import URLError

from tw_stock_screener.cache import Cache, months_before, resolve_date
from tw_stock_screener.config import DEFAULT, load_config
from tw_stock_screener.indicators import aggregate, kd_series, last_kd
from tw_stock_screener.models import Quote, Security, DataError, NoData
from tw_stock_screener.output import export_report
from tw_stock_screener.screener import screen
from tw_stock_screener.sources import parse_daily, parse_isin, number, OfficialSource, open_verified
from tw_stock_screener.universe import load_universe
from tw_stock_screener.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def quote(day=date(2025, 9, 30), **kwargs):
    data = dict(date=day, market="TWSE", code="2330", name="synthetic-test-only",
                open=10., high=12., low=8., close=11., volume=100, change=1.)
    data.update(kwargs)
    return Quote(**data)


def test_history():
    """A deterministic accelerating curve, explicitly synthetic, covering 100 months."""
    days, bars = [], []
    day = date(2015, 1, 1)
    while day <= date(2023, 6, 15):
        if day.weekday() < 5:
            i = len(days)
            close = 100 + (i / 100) ** 2
            days.append(day)
            bars.append(quote(day, open=close - 1, high=close + 2, low=close - 2,
                              close=close, volume=10, change=1.))
        day += timedelta(days=1)
    bars[-1] = replace(bars[-1], volume=32)
    return days, bars


class SourceTests(unittest.TestCase):
    def test_tls_format_fallback_retains_ca_and_hostname_verification(self):
        error = URLError(ssl.SSLCertVerificationError(1, "Missing Subject Key Identifier"))
        with patch("tw_stock_screener.sources.urlopen", side_effect=[error, "response"]) as fetch:
            self.assertEqual(open_verified("https://www.twse.com.tw/", 30), "response")
            context = fetch.call_args.kwargs["context"]
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
            self.assertFalse(context.verify_flags & ssl.VERIFY_X509_STRICT)
        with patch("tw_stock_screener.sources.urlopen", side_effect=URLError(
                ssl.SSLCertVerificationError(1, "certificate has expired"))) as fetch:
            with self.assertRaises(URLError):
                open_verified("https://www.twse.com.tw/", 30)
            self.assertEqual(fetch.call_count, 1)

    def test_real_official_twse_fixture(self):
        doc = json.loads((FIXTURES / "twse_day.json").read_text(encoding="utf-8"))
        rows = parse_daily("TWSE", date(2025, 9, 30), doc)
        tsmc = next(q for q in rows if q.code == "2330")
        self.assertEqual(tsmc.volume, 44853467)
        self.assertEqual(tsmc.close, 1305.)
        self.assertAlmostEqual(tsmc.change_pct, 5 / 1300 * 100)
        self.assertIn("0050", [q.code for q in rows])  # classification happens separately

    def test_real_official_tpex_fixture_and_volume_units(self):
        doc = json.loads((FIXTURES / "tpex_day.json").read_text(encoding="utf-8"))
        rows = parse_daily("TPEX", date(2025, 9, 30), doc)
        q = next(q for q in rows if q.code == "6488")
        self.assertEqual(q.volume, 3000720)
        self.assertEqual(q.change, 8.5)
        monthly = json.loads((FIXTURES / "tpex_month.json").read_text(encoding="utf-8"))
        self.assertIn("成交張數", monthly["tables"][0]["fields"])

    def test_twse_signs_special_change_and_missing_prices(self):
        doc = json.loads((FIXTURES / "twse_day.json").read_text(encoding="utf-8"))
        t = doc["tables"][0]
        t["data"] = [next(r for r in t["data"] if r[0] == "2330")]
        idx = t["fields"].index("漲跌(+/-)")
        t["data"][0][idx] = "<p>-</p>"
        self.assertEqual(parse_daily("TWSE", date(2025, 9, 30), doc)[0].change, -5)
        t["data"][0][idx] = "X"
        self.assertIsNone(parse_daily("TWSE", date(2025, 9, 30), doc)[0].change_pct)
        t["data"][0][t["fields"].index("開盤價")] = "--"
        self.assertFalse(parse_daily("TWSE", date(2025, 9, 30), doc)[0].valid_bar())

    def test_changed_schema_and_wrong_date_fail(self):
        doc = json.loads((FIXTURES / "twse_day.json").read_text(encoding="utf-8"))
        with self.assertRaises(DataError):
            parse_daily("TWSE", date(2025, 10, 1), doc)
        doc["tables"][0]["fields"][0] = "unexpected"
        with self.assertRaises(DataError):
            parse_daily("TWSE", date(2025, 9, 30), doc)

    def test_api_failure_not_a_holiday(self):
        with self.assertRaises(DataError) as ctx:
            parse_daily("TWSE", date.today(), {"stat": "maintenance"})
        self.assertNotIsInstance(ctx.exception, NoData)
        with self.assertRaises(NoData):
            parse_daily("TWSE", date.today(), {"stat": "很抱歉，沒有符合條件的資料!"})

    def test_tpex_official_empty_holiday_shape(self):
        doc = json.loads((FIXTURES / "tpex_day.json").read_text(encoding="utf-8"))
        for t in doc["tables"]:
            t.update(data=[], totalCount=0)
        doc["tables"][0]["listedCompanies"] = "0"
        with self.assertRaises(NoData):
            parse_daily("TPEX", date(2025, 9, 30), doc)
        doc["tables"][0]["listedCompanies"] = "1"
        with self.assertRaises(DataError) as ctx:
            parse_daily("TPEX", date(2025, 9, 30), doc)
        self.assertNotIsInstance(ctx.exception, NoData)

    def test_numeric_sentinels(self):
        for s in ["--", "---", "X0.00", "除息", "NaN", "inf", "", None]:
            self.assertIsNone(number(s))
        self.assertEqual(number("1,234.50"), 1234.5)

    def test_complete_real_isin_classification(self):
        root = Path(__file__).resolve().parents[1] / "docs/probes"
        for market, n in [("TWSE", "twse"), ("TPEX", "tpex")]:
            ds, rows = parse_isin(gzip.decompress((root / f"isin_{n}.html.gz").read_bytes()), market)
            self.assertEqual(ds, date(2026, 10, 10))
            self.assertGreater(len(rows), 800)
            self.assertTrue(all(s.ordinary for s in rows))
            self.assertNotIn("0050", [s.code for s in rows])
            self.assertNotIn("9105", [s.code for s in rows])

    def test_transport_retries_and_raw_provenance(self):
        with TemporaryDirectory() as temp:
            src = OfficialSource(dict(timeout=1, attempts=2, interval_seconds=0), temp)
            with patch("tw_stock_screener.sources.urlopen", side_effect=OSError("blocked")) as fetch, \
                    patch("tw_stock_screener.sources.time.sleep"):
                with self.assertRaises(DataError):
                    src.fetch("https://www.twse.com.tw/test")
                self.assertEqual(fetch.call_count, 2)
            self.assertFalse((Path(temp) / "requests.jsonl").exists())


class IndicatorTests(unittest.TestCase):
    def test_independent_decimal_golden_calculation(self):
        # Fixed 26-bar range [0.5, 100.5]; first RSV=60, next RSV=90.
        bars = [quote(date(2020, 1, 1) + timedelta(days=i), open=50., low=.5,
                      high=100.5, close=60.5 if i < 26 else 90.5) for i in range(27)]
        own = kd_series(bars)
        k1 = Decimal(50) * Decimal(2) / 3 + Decimal(60) / 3
        d1 = Decimal(50) * Decimal(2) / 3 + k1 / 3
        k2 = k1 * Decimal(2) / 3 + Decimal(90) / 3
        d2 = d1 * Decimal(2) / 3 + k2 / 3
        self.assertTrue(all(v["k"] is None for v in own[:25]))
        for i, (k, d) in enumerate([(k1, d1), (k2, d2)], start=25):
            self.assertAlmostEqual(own[i]["k"], float(k), places=12)
            self.assertAlmostEqual(own[i]["d"], float(d), places=12)

    def test_zero_range_and_invalid_bar_fail(self):
        with self.assertRaises(DataError):
            kd_series([quote(open=10, high=10, low=10, close=10)], period=1)
        with self.assertRaises(DataError):
            kd_series([quote(low=20)], period=1)

    def test_partial_week_month_excludes_future_first(self):
        days = [date(2025, 1, 30), date(2025, 1, 31), date(2025, 2, 3), date(2025, 2, 4)]
        rows = [quote(d) for d in days]
        rows[-1] = quote(days[-1], high=999, close=998)
        for frame in ["weekly", "monthly"]:
            bars = aggregate(rows, frame, date(2025, 2, 3))
            self.assertEqual(bars[-1].date, date(2025, 2, 3))
            self.assertEqual(bars[-1].high, 12)
            self.assertEqual(bars[-1].volume, 100)

    def test_week_iso_year_boundary_and_ohlcv(self):
        rows = [quote(date(2024, 12, 30)), quote(date(2025, 1, 2), open=11., close=12., high=13.)]
        bars = aggregate(rows, "weekly", date(2025, 1, 2))
        self.assertEqual(len(bars), 1)
        self.assertEqual((bars[0].open, bars[0].high, bars[0].low, bars[0].close, bars[0].volume),
                         (10., 13., 8., 12., 200))

    def test_warmup_is_enforced(self):
        with self.assertRaises(DataError):
            last_kd([quote()], DEFAULT["kd"])


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.cache = Cache(Path(self.temp.name) / "db.sqlite3")

    def tearDown(self):
        self.cache.close()
        self.temp.cleanup()

    def test_incremental_update_and_future_read_bound(self):
        class Fake:
            calls = 0
            def daily(self, market, day):
                self.calls += 1
                return [quote(day, market=market)]
        fake = Fake()
        self.cache.sync(fake, date(2025, 9, 29), date(2025, 9, 30))
        self.cache.sync(fake, date(2025, 9, 29), date(2025, 9, 30))
        self.assertEqual(fake.calls, 4)
        self.assertEqual(len(self.cache.quotes(date(2025, 9, 29), date(2025, 9, 29))), 2)
        self.cache.sync(fake, date(2025, 9, 29), date(2025, 9, 30), refresh=True)
        self.assertEqual(fake.calls, 8)

    def test_failed_api_does_not_poison_cache(self):
        class Bad:
            def daily(self, market, day):
                raise DataError("API failed")
        with self.assertRaises(DataError):
            self.cache.ensure(Bad(), "TWSE", date(2025, 9, 30))
        self.assertIsNone(self.cache.status("TWSE", date(2025, 9, 30)))
        with self.assertRaises(DataError):
            self.cache.ensure(Bad(), "TWSE", date(2025, 9, 30), offline=True)

    def test_recent_unpublished_report_is_rechecked(self):
        from tw_stock_screener.cli import taipei_today
        day = taipei_today()
        class Publishing:
            published = False
            def daily(self, market, day):
                if not self.published:
                    raise NoData("not published yet")
                return [quote(day, market=market)]
        source = Publishing()
        self.cache.ensure(source, "TWSE", day)
        self.assertEqual(self.cache.status("TWSE", day), "closed")
        source.published = True
        self.cache.ensure(source, "TWSE", day)
        self.assertEqual(self.cache.status("TWSE", day), "open")

    def test_market_disagreement_blocks_latest(self):
        class Mixed:
            def daily(self, market, day):
                if market == "TPEX":
                    raise NoData("closed")
                return [quote(day)]
        with self.assertRaises(DataError):
            resolve_date(self.cache, Mixed(), date(2025, 9, 30))

    def test_latest_and_explicit_nontrading_day(self):
        class Sessions:
            def daily(self, market, day):
                if day.weekday() >= 5:
                    raise NoData("closed")
                return [quote(day, market=market)]
        self.assertEqual(resolve_date(self.cache, Sessions(), date(2025, 10, 5)), date(2025, 10, 3))
        with self.assertRaises(DataError):
            resolve_date(self.cache, Sessions(), date(2025, 10, 5), exact=True)

    def test_atomic_rejection_of_duplicate_or_wrong_date(self):
        day = date(2025, 9, 30)
        for rows in [[quote(), quote()], [quote(date(2025, 10, 1))], []]:
            with self.assertRaises(DataError):
                self.cache.save("TWSE", day, rows)
        self.assertIsNone(self.cache.status("TWSE", day))

    def test_low_disk_does_not_commit_snapshot(self):
        from collections import namedtuple
        usage = namedtuple("Usage", "total used free")(100, 99, 1)
        with patch("tw_stock_screener.cache.shutil.disk_usage", return_value=usage):
            with self.assertRaises(DataError):
                self.cache.save("TWSE", date(2025, 9, 30), [quote()])
        self.assertIsNone(self.cache.status("TWSE", date(2025, 9, 30)))


class ScreeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.days, cls.bars = test_history()
        cls.securities = [Security("TWSE", "2330", "synthetic-test-only", "ESVUFR", date(2010, 1, 1))]

    def run_screen(self, bars=None, cfg=None, **kwargs):
        return screen(self.days[-1], self.bars if bars is None else bars, self.securities,
                      self.days, cfg or DEFAULT, **kwargs)

    def test_all_timeframes_pass_exact_volume_boundary_and_no_minimum(self):
        r = self.run_screen()
        self.assertEqual(len(r["results"]), 1, r["errors"])
        self.assertEqual(r["results"][0]["volume_ratio"], 3.2)
        self.assertEqual(r["results"][0]["volume"], 32)  # deliberately tiny volume
        self.assertEqual(r["counts"]["monthly_kd"], 1)

    def test_ratio_below_boundary_and_previous_zero_fail(self):
        bars = self.bars.copy()
        bars[-1] = replace(bars[-1], volume=31)
        self.assertEqual(self.run_screen(bars)["counts"]["volume_ratio"], 0)
        bars[-2] = replace(bars[-2], volume=0)
        r = self.run_screen(bars)
        self.assertFalse(r["results"])
        self.assertIn("zero", r["errors"][-1]["reason"])

    def test_future_poison_rows_cannot_change_result(self):
        bars = self.bars + [quote(self.days[-1] + timedelta(days=1), high=999999, close=999998)]
        self.assertEqual(self.run_screen(), self.run_screen(bars))

    def test_missing_session_cannot_use_stale_previous_volume(self):
        r = self.run_screen(self.bars[:-2] + self.bars[-1:])
        self.assertFalse(r["results"])
        self.assertIn("missing", r["errors"][-1]["reason"])

    def test_missing_ohlcv_and_unknown_change_fail(self):
        bars = self.bars.copy()
        bars[-1] = replace(bars[-1], close=None)
        self.assertEqual(self.run_screen(bars)["counts"]["rankable"], 0)
        bars[-1] = replace(self.bars[-1], volume=None)
        self.assertFalse(self.run_screen(bars)["results"])

    def test_top_n_precedes_kd_and_does_not_refill(self):
        cfg = deepcopy(DEFAULT)
        cfg["selection"]["top_n"] = 1
        top = quote(self.days[-1], market="TPEX", code="6488", change=9., close=11.)
        sec = Security("TPEX", "6488", "synthetic-test-only", "ESVUFR", date(2010, 1, 1))
        r = screen(self.days[-1], self.bars + [top], self.securities + [sec], self.days, cfg)
        self.assertEqual(r["counts"]["top_n"], 1)
        self.assertEqual(r["candidates"][0]["code"], "6488")
        self.assertFalse(r["results"])

    def test_sequential_kd_equality_fails_and_stops_later_stages(self):
        with patch("tw_stock_screener.screener.last_kd", return_value=(50., 50.)) as calc:
            r = self.run_screen()
        self.assertEqual(calc.call_count, 1)
        self.assertEqual(r["counts"]["daily_kd"], 0)
        self.assertEqual(r["counts"]["weekly_kd"], 0)

    def test_cache_history_loader_matches_inmemory(self):
        r = screen(self.days[-1], self.bars[-1:], self.securities, self.days, DEFAULT,
                   history_loader=lambda market, code: self.bars)
        self.assertEqual(r, self.run_screen())

    def test_nonordinary_cfi_excluded(self):
        securities = self.securities + [Security("TWSE", "0050", "ETF", "CEOGEU", date(2000, 1, 1)),
                                       Security("TWSE", "9105", "TDR", "EDVUFR", date(2000, 1, 1)),
                                       Security("TWSE", "2881A", "preferred", "EPVUFR", date(2000, 1, 1))]
        r = screen(self.days[-1], self.bars, securities, self.days, DEFAULT)
        self.assertEqual(r["counts"]["universe"], 1)


class InterfaceTests(unittest.TestCase):
    def test_config_yaml_and_unknown_keys(self):
        with TemporaryDirectory() as temp:
            p = Path(temp) / "config.yaml"
            p.write_text("selection:\n  top_n: 200\n", encoding="utf-8")
            self.assertEqual(load_config(p)["selection"]["top_n"], 200)
            p.write_text("selection:\n  typo: 3\n", encoding="utf-8")
            with self.assertRaises(DataError):
                load_config(p)
            p.write_text("selection:\n  volume_ratio: .nan\n", encoding="utf-8")
            with self.assertRaises(DataError):
                load_config(p)

    def test_dated_universe_refuses_current_list_for_backtest(self):
        with TemporaryDirectory() as temp:
            p = Path(temp) / "u.json"
            doc = dict(as_of="2026-10-10", source_urls=["https://isin.twse.com.tw/isin/C_public.jsp"],
                       securities=[dict(market=m, code=c, name="test", cfi="ESVUFR", listed_on="2000-01-01")
                                   for m, c in [("TWSE", "2330"), ("TPEX", "6488")]])
            p.write_text(json.dumps(doc), encoding="utf-8")
            with self.assertRaises(DataError):
                load_universe(p, date(2025, 9, 30))
            s, meta = load_universe(p, date(2026, 10, 8), latest=True)
            self.assertEqual(len(s), 2)
            self.assertEqual(meta["mode"], "current_universe")

    def test_empty_report_has_csv_headers_and_unique_outputs(self):
        with TemporaryDirectory() as temp:
            report = {"as_of": "2025-09-30", "results": [], "counts": {}, "errors": []}
            jp, cp = export_report(report, temp)
            self.assertEqual(json.loads(jp.read_text(encoding="utf-8")), report)
            self.assertIn("monthly_d", cp.read_text(encoding="utf-8-sig"))
            self.assertNotEqual(export_report(report, temp)[0], jp)

    def test_cli_failure_artifact_no_false_success_report(self):
        with TemporaryDirectory() as temp:
            cfg = Path(temp) / "config.yaml"
            cfg.write_text("paths:\n  cache: cache.db\n  output: out\n  raw: raw\n", encoding="utf-8")
            code = main(["--config", str(cfg), "screen", "--date", "2025-09-30", "--offline"])
            self.assertEqual(code, 2)
            files = list((Path(temp) / "out").glob("failure-*.json"))
            self.assertEqual(len(files), 1)
            self.assertFalse(list((Path(temp) / "out").glob("screen-*.json")))
            self.assertEqual(json.loads(files[0].read_text())["status"], "failed")

    def test_cli_kd_external_reference_roundtrip_and_mismatch(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            cfg = root / "config.yaml"
            cfg.write_text("paths:\n  cache: cache.db\n  output: out\n  raw: raw\n", encoding="utf-8")
            cache = Cache(root / "cache.db")
            for i in range(30):
                day = date(2025, 1, 1) + timedelta(days=i)
                cache.save("TWSE", day, [quote(day)])
            cache.close()
            common = ["--config", str(cfg), "verify-kd", "--date", "2025-01-30", "--market", "TWSE", "--code", "2330"]
            ref, out = root / "ref.csv", root / "out.csv"
            self.assertEqual(main(common + ["--output", str(ref)]), 0)
            self.assertEqual(main(common + ["--output", str(out), "--reference", str(ref)]), 0)
            ref.write_text("date,k,d\n2025-01-30,0,0\n", encoding="utf-8")
            self.assertEqual(main(common + ["--output", str(out), "--reference", str(ref)]), 2)

    def test_month_boundary(self):
        self.assertEqual(months_before(date(2025, 3, 31), 1), date(2025, 2, 1))


if __name__ == "__main__":
    unittest.main()
