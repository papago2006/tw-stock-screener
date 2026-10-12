"""Synthetic inputs verify fallback boundaries, never real screening outcomes."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tw_stock_screener.cache import Cache
from tw_stock_screener.application import ScreenRequest, run_screen
from tw_stock_screener.config import DEFAULT, load_config
from tw_stock_screener.demand import DemandHistory, CheckedDemandHistory
from tw_stock_screener.models import DataError, NoData, Security
from tw_stock_screener.price_fallback import OfficialPriceFallback
from tw_stock_screener.workflow import compare_replay
from test_core import quote
from test_demand import FakeSource


class PriceFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.cache = Cache(Path(self.temp.name)/"cache.db")
        self.day = date(2025, 9, 1)
        self.original = replace(quote(self.day, volume=None), open=None, high=None, low=None, close=None)
        self.alternative = quote(self.day, volume=999)
        self.source = FakeSource()
        self.source.daily = lambda market, day: [replace(self.alternative, date=day, market=market)]

    def tearDown(self):
        self.cache.close()
        self.temp.cleanup()

    def resolver(self, **kwargs):
        return OfficialPriceFallback(self.cache, self.source, self.day, DEFAULT, **kwargs)

    def test_valid_same_day_prices_fill_without_changing_volume_or_original(self):
        resolver = self.resolver()
        with patch.object(self.source, "daily", wraps=self.source.daily) as fetch:
            effective = resolver.resolve(self.original)
            self.assertEqual(resolver.resolve(self.original), effective)
            self.assertEqual(fetch.call_count, 1)
        self.assertTrue(effective.valid_prices())
        self.assertIsNone(effective.volume)
        self.assertIsNone(self.original.open)
        self.assertEqual(effective.change, self.original.change)
        self.assertEqual(effective.name, self.original.name)
        self.assertEqual(resolver.report()["filled"], 1)
        self.assertIn("www.twse.com.tw/exchangeReport/MI_INDEX", resolver.records[0]["source_url"])
        self.assertEqual(len(resolver.records[0]["daily_quote_sha256"]), 64)

    def test_valid_original_never_fetches_or_gets_overwritten(self):
        resolver = self.resolver()
        with patch.object(self.source, "daily", side_effect=AssertionError("must not fetch")):
            self.assertIs(resolver.resolve(self.alternative), self.alternative)
        self.assertEqual(resolver.report()["attempted"], 0)

    def test_partial_missing_prices_fill_only_when_existing_values_match(self):
        original = replace(self.alternative, open=None, volume=13)
        effective = self.resolver().resolve(original)
        self.assertTrue(effective.valid_prices())
        self.assertEqual(effective.volume, 13)

    def test_conflicting_existing_prices_remain_missing(self):
        original = replace(self.original, close=self.alternative.close + 1)
        resolver = self.resolver()
        self.assertIs(resolver.resolve(original), original)
        self.assertEqual(resolver.records[0]["status"], "conflict")
        self.assertIsNone(resolver.records[0]["replacement_ohlc"])

    def test_complete_but_invalid_candle_is_not_a_missing_value_repair(self):
        original = replace(self.alternative, high=self.alternative.low - 1)
        with patch.object(self.source, "daily", side_effect=AssertionError("must not fetch")):
            self.assertIs(self.resolver().resolve(original), original)

    def test_daily_report_with_missing_prices_does_not_fill(self):
        self.alternative = replace(self.original, volume=538)
        resolver = self.resolver()
        self.assertIs(resolver.resolve(self.original), self.original)
        self.assertEqual(resolver.report()["unresolved"], 1)

    def test_zero_prices_do_not_fill_or_become_a_flat_candle(self):
        self.alternative = replace(self.alternative, open=0, high=0, low=0, close=0)
        self.assertIs(self.resolver().resolve(self.original), self.original)

    def test_wrong_stock_and_missing_stock_do_not_fill(self):
        self.alternative = replace(self.alternative, code="9999")
        self.assertIs(self.resolver().resolve(self.original), self.original)

    def test_future_original_is_rejected_before_io(self):
        future = replace(self.original, date=date(2025, 9, 2))
        with patch.object(self.source, "daily", side_effect=AssertionError("must not fetch")):
            with self.assertRaisesRegex(DataError, "Future"):
                self.resolver().resolve(future)

    def test_wrong_date_daily_response_does_not_fill(self):
        self.source.daily = lambda *a: [replace(self.alternative, date=date(2025, 9, 2))]
        resolver = self.resolver()
        self.assertIs(resolver.resolve(self.original), self.original)
        self.assertEqual(resolver.records[0]["status"], "source_error")

    def test_cached_report_is_reused_offline_and_provenance_replays(self):
        online = self.resolver()
        result = online.resolve(self.original)
        offline = self.resolver(offline=True)
        with patch.object(self.source, "daily", side_effect=AssertionError("HTTP forbidden")):
            self.assertEqual(offline.resolve(self.original), result)
        self.assertEqual(online.report(), offline.report())

    def test_missing_offline_cache_keeps_null_and_never_calls_source(self):
        with patch.object(self.source, "daily", side_effect=AssertionError("HTTP forbidden")):
            resolver = self.resolver(offline=True)
            self.assertIs(resolver.resolve(self.original), self.original)
        self.assertEqual(resolver.records[0]["status"], "source_error")

    def test_api_failure_remains_an_error_and_does_not_repeat_within_stage(self):
        resolver = self.resolver()
        with patch.object(self.source, "daily", side_effect=DataError("Official API failed: test timeout")) as fetch:
            resolver.resolve(self.original)
            resolver.resolve(replace(self.original, code="9999"))
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(resolver.report()["filled"], 0)
        self.assertEqual(resolver.report()["unresolved"], 2)

    def test_no_data_day_never_creates_a_price(self):
        with patch.object(self.source, "daily", side_effect=NoData("no data")):
            self.assertIs(self.resolver().resolve(self.original), self.original)

    def test_local_save_failure_stops_instead_of_becoming_an_exclusion(self):
        with patch.object(self.cache, "save", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.resolver().resolve(self.original)

    def test_one_market_report_serves_multiple_stocks_without_repeated_fetches(self):
        other = replace(self.alternative, code="9999")
        with patch.object(self.source, "daily", return_value=[self.alternative, other]) as fetch:
            resolver = self.resolver()
            self.assertTrue(resolver.resolve(self.original).valid_prices())
            self.assertTrue(resolver.resolve(replace(self.original, code="9999")).valid_prices())
        self.assertEqual(fetch.call_count, 1)

    def test_unresolved_first_date_stops_other_repair_requests(self):
        self.alternative = self.original
        resolver = self.resolver()
        rows = [self.original, replace(self.original, code="9999")]
        self.assertEqual(resolver.complete(rows), rows)
        self.assertEqual(resolver.report()["attempted"], 1)

    def test_explicit_none_preserves_previous_no_fallback_behavior(self):
        config = deepcopy(DEFAULT)
        config["data_quality"]["ohlc_fallback"] = "none"
        resolver = OfficialPriceFallback(self.cache, self.source, self.day, config)
        with patch.object(self.source, "daily", side_effect=AssertionError("must not fetch")):
            self.assertIs(resolver.resolve(self.original), self.original)

    def test_configuration_refuses_unapproved_vendor_or_fill_policy(self):
        path = Path(self.temp.name)/"config.yaml"
        for value in ("finmind", "forward_fill", "adjusted", ""):
            path.write_text(f'data_quality:\n  ohlc_fallback: "{value}"\n', encoding="utf-8")
            with self.assertRaises(DataError):
                load_config(path)


class ProviderFallbackTests(unittest.TestCase):
    def test_application_records_effective_prices_policy_and_exact_offline_replay(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            cache = Cache(root/"cache.db")
            source = FakeSource()
            target = date(2025, 9, 15)
            cfg = deepcopy(DEFAULT)
            cfg["selection"]["top_n"] = 1
            universe = root/"universe.json"
            universe.write_text(json.dumps({"as_of": str(target),
                "source_urls": ["https://isin.twse.com.tw/isin/C_public.jsp"],
                "securities": [{"market": m, "code": c, "name": "synthetic", "cfi": "ESVUFR",
                                "listed_on": "2025-09-01"}
                               for m,c in [("TWSE", "2330"), ("TPEX", "6488")]]}), encoding="utf-8")
            for day in (date(2025, 9, 12), target):
                cache.save("TWSE", day, [quote(day)])
                cache.save("TPEX", day, [quote(day, market="TPEX", code="6488", change=0)])
            real_monthly = source.monthly
            def synthetic_missing_month(*args):
                rows = real_monthly(*args)
                rows[0] = replace(rows[0], open=None, high=None, low=None, close=None)
                return rows
            source.monthly = synthetic_missing_month
            request = ScreenRequest(date=target, universe=str(universe))
            try:
                report = run_screen(request, cfg, cache, source, target,
                                    history_factory=CheckedDemandHistory)
                self.assertEqual(report["data_quality"]["filled"], 1)
                with patch.object(source, "daily", side_effect=AssertionError("HTTP forbidden")), \
                     patch.object(source, "monthly", side_effect=AssertionError("HTTP forbidden")), \
                     patch.object(source, "trading_month", side_effect=AssertionError("HTTP forbidden")):
                    replay = run_screen(replace(request, offline=True), cfg, cache, source, target,
                                        history_factory=CheckedDemandHistory)
                self.assertTrue(compare_replay(report, replay)["matched"])
                self.assertEqual(report["data_quality"], replay["data_quality"])
                old_cfg = deepcopy(cfg)
                old_cfg["data_quality"]["ohlc_fallback"] = "none"
                old = run_screen(replace(request, offline=True), old_cfg, cache, source, target,
                                 history_factory=CheckedDemandHistory)
                self.assertNotEqual(old["provenance"]["rules_sha256"],
                                    report["provenance"]["rules_sha256"])
                # Only 11 synthetic sessions: filling a price must not bypass
                # the existing 86-candle warmup requirement.
                self.assertFalse(report["results"])
                self.assertIn("warmup", report["errors"][0]["reason"])
                self.assertIn("Invalid/missing", old["errors"][0]["reason"])
            finally:
                cache.close()

    def test_both_providers_use_fallback_without_rewriting_month_payload(self):
        for provider_type in (DemandHistory, CheckedDemandHistory):
            with self.subTest(provider=provider_type.__name__), TemporaryDirectory() as temp:
                cache = Cache(Path(temp)/"cache.db")
                source = FakeSource()
                as_of = date(2025, 9, 15)
                security = Security("TWSE", "2330", "synthetic", "ESVUFR", date(2025, 9, 1))
                provider = provider_type(cache, source, as_of, [security], DEFAULT)
                rows = source.monthly("TWSE", "2330", date(2025, 9, 1), as_of)
                rows[0] = replace(rows[0], open=None, high=None, low=None, close=None)
                payload = json.dumps([q.record() for q in rows])
                with cache.db:
                    cache.db.execute("INSERT INTO stock_months VALUES (?,?,?,?,?)",
                        ("TWSE", "2330", "2025-09-01", str(as_of), payload))
                try:
                    effective, calendar = provider.stage("TWSE", "2330", "daily")
                    self.assertTrue(all(q.valid_prices() for q in effective))
                    self.assertEqual(len(effective), len(calendar))
                    self.assertEqual(provider.price_fallback.report()["filled"], 1)
                    self.assertIsNone(effective[0].volume)
                    saved = cache.db.execute("SELECT payload FROM stock_months").fetchone()[0]
                    self.assertEqual(saved, payload)
                    offline = provider_type(cache, source, as_of, [security], DEFAULT, offline=True)
                    with patch.object(source, "daily", side_effect=AssertionError("HTTP forbidden")):
                        replay, _ = offline.stage("TWSE", "2330", "daily")
                    self.assertEqual(replay, effective)
                    self.assertEqual(offline.price_fallback.report(), provider.price_fallback.report())
                finally:
                    cache.close()

    def test_missing_sessions_are_not_silently_filled_or_skipped(self):
        with TemporaryDirectory() as temp:
            cache = Cache(Path(temp)/"cache.db")
            source = FakeSource()
            security = Security("TWSE", "2330", "synthetic", "ESVUFR", date(2025, 9, 1))
            provider = CheckedDemandHistory(cache, source, date(2025, 9, 15), [security], DEFAULT)
            try:
                provider.stock_month("TWSE", "2330", date(2025, 9, 1))
                with cache.db:
                    cache.db.execute("UPDATE stock_months SET payload='[]'")
                provider.month_memory.clear()
                with patch.object(source, "daily", side_effect=AssertionError("must not fetch")):
                    with self.assertRaisesRegex(DataError, "History missing"):
                        provider.stage("TWSE", "2330", "daily")
            finally:
                cache.close()


if __name__ == "__main__":
    unittest.main()
