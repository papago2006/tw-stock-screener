"""Read-only real-cache sample validation; no HTTP and no result approval.

All mutations occur in a SQLite in-memory backup. Existing reports, source
databases and raw archives stay untouched. Raw evidence is hash-verified.
"""
from argparse import ArgumentParser
from copy import deepcopy
from dataclasses import replace
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

from tw_stock_screener.application import ScreenRequest, run_screen
from tw_stock_screener.config import load_config
from tw_stock_screener.demand import CheckedDemandHistory
from tw_stock_screener.models import DataError, Quote
from tw_stock_screener.output import export_report, write_json
from tw_stock_screener.price_fallback import OfficialPriceFallback
from tw_stock_screener.recovery import ArchiveFirstSource, RecoveryCache
from tw_stock_screener.sources import OfficialSource
from tw_stock_screener.workflow import compare_replay


def validate(args):
    cfg = load_config(args.config)
    baseline_path = Path(args.baseline_report).resolve()
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    target = date.fromisoformat(baseline["as_of"])
    reference = date.fromisoformat(baseline["universe"]["as_of"])
    cache_path = Path(cfg["paths"]["cache"]).resolve()
    output = Path(args.output).resolve()
    if output.exists():
        raise DataError("Validation output must be a new directory")
    cache_hash = hashlib.sha256(cache_path.read_bytes()).hexdigest()
    cache = RecoveryCache(":memory:", target)
    try:
        with sqlite3.connect(cache_path.as_uri() + "?mode=ro", uri=True) as original:
            original.backup(cache.db)
        source = ArchiveFirstSource(cfg["network"], args.probe_raw)
        checks = []
        # The earlier online probe is deliberately fixed to four real gaps.
        # Do not issue another network request or enlarge this sample.
        with patch.object(OfficialSource, "fetch", side_effect=AssertionError("HTTP prohibited")):
            for market, code, stamp in [("TWSE", "2424", "2024-12-17"),
                                        ("TPEX", "3287", "2016-11-18"),
                                        ("TPEX", "8086", "2020-08-06"),
                                        ("TPEX", "5205", "2026-04-28")]:
                day = date.fromisoformat(stamp)
                row = cache.db.execute(
                    "SELECT payload FROM stock_months WHERE market=? AND code=? AND month=?",
                    (market, code, str(day.replace(day=1)))).fetchone()
                quotes = ([Quote(**{**q, "date": date.fromisoformat(q["date"])})
                           for q in json.loads(row[0]) if q["date"] == stamp] if row else
                          cache.quotes(day, day, market, code))
                if len(quotes) != 1 or quotes[0].valid_prices():
                    raise DataError("Selected probe is not a unique real missing-price row")
                cache.save(market, day, source.daily(market, day))
                resolver = OfficialPriceFallback(cache, None, target, cfg, offline=True)
                effective = resolver.resolve(quotes[0])
                checks.append({**resolver.report(), "effective_ohlc_valid": effective.valid_prices(),
                               "original_unchanged": effective == quotes[0]})

            request = ScreenRequest(universe=args.universe, offline=True, evaluation_limit=30)
            old_cfg = deepcopy(cfg)
            old_cfg["data_quality"]["ohlc_fallback"] = "none"
            old = run_screen(request, old_cfg, cache, source, reference,
                             history_factory=CheckedDemandHistory)
            sample = run_screen(request, cfg, cache, source, reference,
                                history_factory=CheckedDemandHistory)
            replay = run_screen(replace(request, offline=True), cfg, cache, source, reference,
                                history_factory=CheckedDemandHistory)
        verification = compare_replay(sample, replay)
        fields = ("as_of", "previous_session", "counts", "candidates", "results", "errors", "validation")
        unchanged = {key: sample[key] == old[key] for key in fields}
        baseline_match = sample["candidates"] == baseline["candidates"][:30]
        original_preserved = hashlib.sha256(cache_path.read_bytes()).hexdigest() == cache_hash
        summary = {"as_of": str(target), "report_scope": "validation_sample",
                   "publication_status": "validation_only", "evaluated": len(sample["candidates"]),
                   "official_probe_count": len(checks),
                   "official_probe_filled": sum(c["filled"] for c in checks),
                   "sample_fallback": {k: v for k,v in sample["data_quality"].items() if k != "records"},
                   "baseline_first_30_candidates_match": baseline_match,
                   "old_policy_comparison": unchanged, "offline_replay": verification,
                   "network_calls": source.network_calls, "verified_archive_hits": source.archive_hits,
                   "original_cache_sha256_unchanged": original_preserved,
                   "external_kd_software_verified": False, "result_version_created": False,
                   "evidence": checks}
        if (not verification["matched"] or not all(unchanged.values()) or not baseline_match
                or not original_preserved or source.network_calls):
            raise DataError("Real sample validation did not match; no result saved")
        output.mkdir(parents=True)
        sample.update(publication_status="validation_only", verification=verification)
        export_report(sample, output)
        write_json(output/"summary.json", summary)
        print(json.dumps({k:v for k,v in summary.items() if k != "evidence"}, ensure_ascii=False, indent=2))
    finally:
        cache.close()


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="local.yaml")
    parser.add_argument("--baseline-report", required=True)
    parser.add_argument("--universe", required=True)
    parser.add_argument("--probe-raw", required=True)
    parser.add_argument("--output", required=True)
    validate(parser.parse_args())
