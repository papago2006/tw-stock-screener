"""On-demand scheduling/coverage tests. Synthetic prices are only test inputs."""
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from tw_stock_screener.cache import Cache
from tw_stock_screener.config import DEFAULT
from tw_stock_screener.demand import DemandHistory, cached_prices
from tw_stock_screener.models import Quote, Security, DataError
from tw_stock_screener.sources import parse_monthly, parse_trading_month
from tw_stock_screener.screener import screen
from tw_stock_screener.indicators import aggregate, kd_series
from tw_stock_screener.cli import main
from test_core import test_history, quote

FIXTURES = Path(__file__).parent / "fixtures"


class MonthlyParserTests(unittest.TestCase):
    def test_real_twse_month_cutoff_and_exact_shares(self):
        doc = json.loads((FIXTURES / "twse_month.json").read_text(encoding="utf-8"))
        rows = parse_monthly("TWSE", "2330", date(2025, 9, 1), date(2025, 9, 15), doc)
        self.assertEqual(rows[-1].date, date(2025, 9, 15))
        self.assertEqual(rows[0].volume, 23022319)
        self.assertTrue(all(q.date <= date(2025, 9, 15) for q in rows))
        with self.assertRaises(DataError):
            parse_monthly("TWSE", "9999", date(2025, 9, 1), date(2025, 9, 15), doc)

    def test_real_tpex_lots_never_become_exact_shares(self):
        doc = json.loads((FIXTURES / "tpex_month.json").read_text(encoding="utf-8"))
        rows = parse_monthly("TPEX", "6488", date(2025, 9, 1), date(2025, 9, 30), doc)
        self.assertEqual(rows[-1].close, 466.5)
        self.assertTrue(all(q.volume is None for q in rows))
        # Volume is irrelevant to RSV/KD; unknown volume stays unknown, never zero-filled.
        self.assertIsNone(aggregate(rows, "monthly", date(2025, 9, 30))[0].volume)
        self.assertIsNotNone(kd_series(rows, period=3)[-1]["k"])

    def test_real_official_calendar_and_future_cutoff(self):
        doc = json.loads((FIXTURES / "twse_calendar.json").read_text(encoding="utf-8"))
        days = parse_trading_month(date(2025, 9, 1), date(2025, 9, 26), doc)
        self.assertEqual(days[-1], date(2025, 9, 26))
        self.assertNotIn(date(2025, 9, 29), days)  # official holiday, not a guessed weekday
        with self.assertRaises(DataError):
            parse_trading_month(date(2025, 8, 1), date(2025, 9, 26), doc)


class FakeSource:
    """Explicit test double, no claims of exchange data."""
    def __init__(self):
        self.history_calls, self.calendar_calls, self.daily_calls = [], [], []
        self.fail = False

    def trading_month(self, month, as_of):
        self.calendar_calls.append(month)
        result, day = [], month
        while day.month == month.month and day <= as_of:
            if day.weekday() < 5:
                result.append(day)
            day += timedelta(days=1)
        return result

    def monthly(self, market, code, month, as_of):
        self.history_calls.append((market, code, month, as_of))
        if self.fail:
            raise DataError("API failed")
        days = []
        day = month
        while day.month == month.month and day <= as_of:
            if day.weekday() < 5:
                days.append(day)
            day += timedelta(days=1)
        return [quote(d, market=market, code=code, volume=None) for d in days]

    def daily(self, market, day):
        self.daily_calls.append((market, day))
        return [quote(day, market=market, volume=7)]


class DemandCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.cache = Cache(Path(self.temp.name) / "cache.db")
        self.source = FakeSource()
        self.secs = [Security("TWSE", "2330", "synthetic", "ESVUFR", date(2000, 1, 1))]

    def tearDown(self):
        self.cache.close()
        self.temp.cleanup()

    def provider(self, as_of=date(2025, 9, 15), **kwargs):
        return DemandHistory(self.cache, self.source, as_of, self.secs, DEFAULT, **kwargs)

    def test_repeated_stock_month_is_cached_across_process_instances(self):
        first = self.provider().stock_month("TWSE", "2330", date(2025, 9, 1))
        second = self.provider(offline=True).stock_month("TWSE", "2330", date(2025, 9, 1))
        self.assertEqual(first, second)
        self.assertEqual(len(self.source.history_calls), 1)
        self.assertIsNone(self.cache.status("TWSE", date(2025, 9, 15)))

    def test_later_cutoff_refetches_partial_month_earlier_never_leaks_future(self):
        self.provider().stock_month("TWSE", "2330", date(2025, 9, 1))
        later = self.provider(date(2025, 9, 30)).stock_month("TWSE", "2330", date(2025, 9, 1))
        self.assertEqual(later[-1].date, date(2025, 9, 30))
        earlier = self.provider(date(2025, 9, 10), offline=True).stock_month("TWSE", "2330", date(2025, 9, 1))
        self.assertEqual(earlier[-1].date, date(2025, 9, 10))
        self.assertEqual(len(self.source.history_calls), 2)

    def test_calendar_shared_cached_and_partial_month_rechecked(self):
        first = self.provider().calendar(date(2025, 9, 1), date(2025, 9, 15))
        second = self.provider(offline=True).calendar(date(2025, 9, 1), date(2025, 9, 15))
        self.assertEqual(first, second)
        self.assertEqual(len(self.source.calendar_calls), 1)
        later = self.provider(date(2025, 9, 30)).calendar(date(2025, 9, 1), date(2025, 9, 30))
        self.assertEqual(later[-1], date(2025, 9, 30))
        self.assertEqual(len(self.source.calendar_calls), 2)

    def test_failed_month_is_not_cached_as_complete(self):
        self.source.fail = True
        with self.assertRaises(DataError):
            self.provider().stock_month("TWSE", "2330", date(2025, 9, 1))
        self.assertEqual(self.cache.db.execute("SELECT count(*) FROM stock_months").fetchone()[0], 0)

    def test_previous_volume_comes_from_exact_daily_report_only(self):
        provider = self.provider(refresh=True)
        prior = provider.previous_quote("TWSE", "2330", date(2025, 9, 12))
        provider.previous_quote("TWSE", "2330", date(2025, 9, 12))
        self.assertEqual(prior.volume, 7)
        self.assertEqual(len(self.source.daily_calls), 2)
        self.assertFalse(self.source.history_calls)

    def test_timeframe_ranges_extend_only_when_requested_and_reuse_overlap(self):
        provider = self.provider()
        provider.stage("TWSE", "2330", "daily")
        self.assertEqual(len(self.source.history_calls), 7)
        self.assertEqual(min(c[2] for c in self.source.history_calls), date(2025, 3, 1))
        provider.stage("TWSE", "2330", "weekly")
        self.assertEqual(len(self.source.history_calls), 25)
        self.assertEqual(min(c[2] for c in self.source.history_calls), date(2023, 9, 1))
        provider.stage("TWSE", "2330", "monthly")
        self.assertEqual(len(self.source.history_calls), 121)
        self.assertEqual(min(c[2] for c in self.source.history_calls), date(2015, 9, 1))

    def test_verification_reads_month_cache_and_prefers_exact_daily(self):
        p = self.provider()
        p.stock_month("TWSE", "2330", date(2025, 9, 1))
        self.cache.save("TWSE", date(2025, 9, 15), [quote(date(2025, 9, 15), volume=99)])
        rows = cached_prices(self.cache, date(2025, 9, 1), date(2025, 9, 15), "TWSE", "2330")
        self.assertEqual(rows[-1].volume, 99)
        self.assertTrue(all(q.date <= date(2025, 9, 15) for q in rows))


class StagedSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.days, cls.bars = test_history()
        cls.as_of = cls.days[-1]
        cls.secs = [Security("TWSE", "2330", "synthetic", "ESVUFR", date(2010, 1, 1))]

    def run_staged(self, kd_values, volume=10, bars=None):
        calls, volume_calls = [], []
        history = self.bars if bars is None else bars
        def stage(market, code, frame):
            calls.append((market, code, frame))
            return history, self.days
        def previous(market, code, day):
            volume_calls.append(day)
            return replace(self.bars[-2], volume=volume)
        with patch("tw_stock_screener.screener.last_kd", side_effect=kd_values):
            r = screen(self.as_of, self.bars[-1:], self.secs, self.days[-2:], DEFAULT,
                       stage_loader=stage, volume_loader=previous)
        return r, calls, volume_calls

    def test_daily_failure_never_loads_week_month_or_previous_volume(self):
        r, calls, vol = self.run_staged([(40, 50)])
        self.assertEqual([c[2] for c in calls], ["daily"])
        self.assertFalse(vol)
        self.assertFalse(r["results"])

    def test_weekly_failure_never_loads_month_or_previous_volume(self):
        r, calls, vol = self.run_staged([(60, 50), (40, 50)])
        self.assertEqual([c[2] for c in calls], ["daily", "weekly"])
        self.assertFalse(vol)
        self.assertEqual(r["counts"]["daily_kd"], 1)
        self.assertEqual(r["counts"]["weekly_kd"], 0)

    def test_all_kd_pass_uses_exact_volume_and_keeps_ratio_threshold(self):
        # Unknown monthly historical volume must not be interpreted as exact volume.
        bars = [replace(b, volume=None) for b in self.bars]
        r, calls, vol = self.run_staged([(60, 50)] * 3, bars=bars)
        self.assertEqual([c[2] for c in calls], ["daily", "weekly", "monthly"])
        self.assertEqual(vol, [self.days[-2]])
        self.assertEqual(r["results"][0]["volume_ratio"], 3.2)
        self.assertEqual(r["results"][0]["volume"], 32)

    def test_missing_price_session_fails_before_any_kd(self):
        r, calls, vol = self.run_staged([], bars=self.bars[:-2] + self.bars[-1:])
        self.assertFalse(vol)
        self.assertFalse(r["results"])
        self.assertIn("missing", r["errors"][0]["reason"])

    def test_history_api_failure_recorded_per_stock(self):
        def bad(*args):
            raise DataError("API failed")
        r = screen(self.as_of, self.bars[-1:], self.secs, self.days[-2:], DEFAULT, stage_loader=bad)
        self.assertFalse(r["results"])
        self.assertEqual(r["errors"][0]["stage"], "daily_kd")

    def test_outside_top_n_never_loads_history_and_no_refill(self):
        other = replace(self.bars[-1], code="9999", change=0)
        secs = self.secs + [Security("TWSE", "9999", "synthetic", "ESVUFR", date(2010, 1, 1))]
        import copy
        cfg = copy.deepcopy(DEFAULT)
        cfg["selection"]["top_n"] = 1
        called = []
        def stage(market, code, frame):
            called.append(code)
            return self.bars, self.days
        with patch("tw_stock_screener.screener.last_kd", return_value=(40, 50)):
            r = screen(self.as_of, [self.bars[-1], other], secs, self.days[-2:], cfg, stage_loader=stage)
        self.assertEqual(called, ["2330"])
        self.assertEqual(r["counts"]["top_n"], 1)

    def test_target_price_mismatch_fails(self):
        bars = self.bars.copy()
        bars[-1] = replace(bars[-1], close=bars[-1].close + .5)
        r, calls, vol = self.run_staged([], bars=bars)
        self.assertFalse(r["results"])
        self.assertIn("differs", r["errors"][0]["reason"])

    def test_cli_uses_stages_and_never_calls_full_market_sync(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            cfg = root / "config.yaml"
            cfg.write_text("selection:\n  top_n: 1\npaths:\n  cache: cache.db\n  output: out\n  raw: raw\n", encoding="utf-8")
            cache = Cache(root / "cache.db")
            for day, own in [(self.days[-2], self.bars[-2]), (self.as_of, self.bars[-1])]:
                cache.save("TWSE", day, [own])
                cache.save("TPEX", day, [replace(own, market="TPEX", code="6488", change=0.)])
            cache.close()
            uni = root / "universe.json"
            uni.write_text(json.dumps({"as_of": str(self.as_of),
                "source_urls": ["https://isin.twse.com.tw/isin/C_public.jsp"],
                "securities": [{"market": m, "code": c, "name": "synthetic", "cfi": "ESVUFR",
                                "listed_on": "2010-01-01"} for m,c in [("TWSE","2330"),("TPEX","6488")]]}), encoding="utf-8")
            with patch("tw_stock_screener.cli.taipei_today", return_value=self.as_of), \
                 patch.object(Cache, "sync", side_effect=AssertionError("full sync must never run")), \
                 patch.object(DemandHistory, "calendar", return_value=self.days[-2:]), \
                 patch.object(DemandHistory, "stage", return_value=(self.bars, self.days)) as stages:
                code = main(["--config", str(cfg), "screen", "--offline", "--universe", str(uni)])
            self.assertEqual(code, 0)
            self.assertEqual([c.args[2] for c in stages.call_args_list], ["daily","weekly","monthly"])
            report = json.loads(next((root / "out").glob("screen-*.json")).read_text(encoding="utf-8"))
            self.assertEqual(report["history_mode"], "on_demand_by_stage")
            self.assertEqual(report["results"][0]["volume_ratio"], 3.2)
            from tw_stock_screener.results import SQLiteResultStore
            with SQLiteResultStore(root / "data" / "results.sqlite3") as store:
                saved = store.get(report["result_version"])
                self.assertEqual(saved["report"]["results"], report["results"])
                self.assertIsNone(store.official(str(self.as_of)))
                self.assertEqual(len(saved["report"]["provenance"]["inputs"]["stages"]), 3)
                self.assertEqual(len(saved["report"]["provenance"]["inputs"]["previous_quotes"]), 1)


if __name__ == "__main__":
    unittest.main()
