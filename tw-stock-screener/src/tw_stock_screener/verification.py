"""Cached KD export and external-reference comparison; never fetches行情."""
from pathlib import Path
import csv
from .cache import months_before
from .demand import cached_prices
from .indicators import aggregate, kd_series
from .models import DataError


def verify(args, cache, cfg):
    key = {"daily": "daily_months", "weekly": "weekly_months", "monthly": "months"}[args.timeframe]
    start = months_before(args.date, cfg["history"][key])
    history = cached_prices(cache, start, args.date, args.market, args.code)
    if not history:
        raise DataError("No cached history for KD verification")
    bars = aggregate(history, args.timeframe, args.date)
    values = kd_series(bars, **{k: v for k, v in cfg["kd"].items() if k != "warmup_periods"})
    fields = ["date", "open", "high", "low", "close", "volume", "rsv", "k", "d"]
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for bar, value in zip(bars, values):
            writer.writerow({**{k: getattr(bar, k) for k in fields[1:6]}, **value})
    if args.reference:
        import math
        if not math.isfinite(args.tolerance) or args.tolerance < 0:
            raise DataError("Tolerance must be finite and non-negative")
        own = {v["date"]: v for v in values if v["k"] is not None}
        compared, seen = 0, set()
        with Path(args.reference).open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if not row.get("k") or not row.get("d"):
                    continue
                ds = row["date"]
                if ds not in own or ds in seen:
                    raise DataError(f"Unknown/duplicate reference date: {ds}")
                seen.add(ds)
                for key in ["k", "d"]:
                    value = float(row[key])
                    if not math.isfinite(value) or abs(own[ds][key] - value) > args.tolerance:
                        raise DataError(f"KD reference mismatch: {ds} {key}")
                compared += 1
        if not compared:
            raise DataError("No comparable reference rows")
        print(f"Reference matched: {compared} rows")
    print(path.resolve())


