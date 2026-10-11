from dataclasses import replace
from .models import DataError


def aggregate(quotes, timeframe, as_of):
    """Truncate BEFORE aggregation; date labels are the last observed session."""
    if timeframe not in {"daily", "weekly", "monthly"}:
        raise DataError(f"Unknown timeframe: {timeframe}")
    groups = {}
    filtered = sorted((q for q in quotes if q.date <= as_of), key=lambda q: q.date)
    seen = set()
    for q in filtered:
        if q.date in seen or not q.valid_prices():
            raise DataError("Duplicate or invalid OHLCV in indicator history")
        seen.add(q.date)
        if timeframe == "daily":
            key = q.date
        elif timeframe == "weekly":
            key = q.date.isocalendar()[:2]  # Monday through Sunday, including year boundaries
        else:
            key = (q.date.year, q.date.month)
        groups.setdefault(key, []).append(q)
    return [replace(g[-1], open=g[0].open, high=max(q.high for q in g),
                    low=min(q.low for q in g),
                    volume=sum(q.volume for q in g) if all(q.volume is not None for q in g) else None,
                    change=None)
            for g in groups.values()]


def kd_series(bars, period=26, k_smoothing=3, d_smoothing=3, initial=50.0):
    """Full-window RSV, then recursive K,D. No rounding until presentation."""
    if period < 1 or k_smoothing < 1 or d_smoothing < 1 or not 0 <= initial <= 100:
        raise DataError("Invalid KD parameters")
    if any(not b.valid_prices() for b in bars):
        raise DataError("Invalid bars")
    if any(a.date >= b.date for a, b in zip(bars, bars[1:])):
        raise DataError("KD bars must be strictly chronological")
    k = d = initial
    result = []
    for i, bar in enumerate(bars):
        if i + 1 < period:
            result.append({"date": bar.date.isoformat(), "rsv": None, "k": None, "d": None})
            continue
        window = bars[i + 1 - period:i + 1]
        low, high = min(b.low for b in window), max(b.high for b in window)
        if high == low:
            # Undefined RSV must never create a passing result.
            raise DataError(f"Zero high-low range at {bar.date}")
        rsv = 100 * (bar.close - low) / (high - low)
        k += (rsv - k) / k_smoothing
        d += (k - d) / d_smoothing
        result.append({"date": bar.date.isoformat(), "rsv": rsv, "k": k, "d": d})
    return result


def last_kd(bars, config):
    required = config["period"] + config["warmup_periods"]
    if len(bars) < required:
        raise DataError(f"Insufficient warmup: {len(bars)} bars, need {required}")
    args = {k: v for k, v in config.items() if k != "warmup_periods"}
    last = kd_series(bars, **args)[-1]
    return last["k"], last["d"]
