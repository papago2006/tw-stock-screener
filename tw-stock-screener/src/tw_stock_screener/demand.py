"""On-demand per-stock daily prices and shared official market calendar.

Coverage is tracked separately from whole-market snapshots: downloading one
stock must never mark its market/day as complete. Partial months are refetched
when a later cutoff needs more days.
"""
from datetime import date, timedelta
from calendar import monthrange
import json
import logging
import shutil
from .cache import MARKETS, months_before
from .models import Quote, DataError
from .price_fallback import OfficialPriceFallback

LOG = logging.getLogger(__name__)


def months(start, end):
    current = start.replace(day=1)
    while current <= end:
        yield current
        current = months_before(current, -1)


def end_of_month(month):
    return month.replace(day=monthrange(month.year, month.month)[1])


def cached_prices(cache, start, end, market, code):
    merged = {}
    exists = cache.db.execute("SELECT name FROM sqlite_master WHERE name='stock_months'").fetchone()
    if exists:
        for (payload,) in cache.db.execute(
                "SELECT payload FROM stock_months WHERE market=? AND code=? AND month>=? AND month<=?",
                (market, code, start.replace(day=1).isoformat(), end.isoformat())):
            for item in json.loads(payload):
                item["date"] = date.fromisoformat(item["date"])
                if start <= item["date"] <= end:
                    q = Quote(**item)
                    merged[q.date] = q
    for q in cache.quotes(start, end, market, code):
        merged[q.date] = q
    return sorted(merged.values(), key=lambda q: q.date)


class DemandHistory:
    def __init__(self, cache, source, as_of, securities, config, offline=False, refresh=False):
        self.cache, self.source, self.as_of = cache, source, as_of
        self.config, self.offline, self.refresh = config, offline, refresh
        self.securities = {(s.market, s.code): s for s in securities}
        self.calendar_memory, self.month_memory = {}, {}
        self.precise_days = set()
        self.price_fallback = OfficialPriceFallback(cache, source, as_of, config,
                                                   offline, refresh, self.reserve)
        cache.db.executescript("""
          CREATE TABLE IF NOT EXISTS calendar_months (
            month TEXT PRIMARY KEY, through TEXT NOT NULL, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS stock_months (
            market TEXT, code TEXT, month TEXT, through TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(market,code,month));
        """)

    def reserve(self):
        if shutil.disk_usage(self.cache.directory).free < 1024 ** 3:
            raise DataError("Less than 1 GiB free disk reserve; free space before resuming")

    def covered_market_month(self, market, month, through):
        day = month
        while day <= through:
            if self.cache.status(market, day) is None:
                return False
            day += timedelta(days=1)
        return True

    def calendar(self, start, end):
        end = min(end, self.as_of)
        result = []
        for month in months(start, end):
            if month < date(2010, 1, 1):
                raise DataError("Calendar/history earlier than 2010 needs verified official archives")
            through = min(end_of_month(month), self.as_of)
            key = month.isoformat()
            if key not in self.calendar_memory:
                if not self.refresh and all(self.covered_market_month(m, month, through) for m in MARKETS):
                    days = self.cache.calendar(month, through)
                else:
                    cached = self.cache.db.execute(
                        "SELECT through,payload FROM calendar_months WHERE month=?", (key,)).fetchone()
                    if cached and cached[0] >= through.isoformat() and not self.refresh:
                        days = [date.fromisoformat(d) for d in json.loads(cached[1])]
                    else:
                        if self.offline:
                            raise DataError(f"Official trading calendar missing: {month}")
                        days = self.source.trading_month(month, self.as_of)
                        if any(d > self.as_of or d.replace(day=1) != month for d in days):
                            raise DataError("Calendar source returned future/wrong-month dates")
                        self.reserve()
                        with self.cache.db:
                            self.cache.db.execute("INSERT OR REPLACE INTO calendar_months VALUES (?,?,?)",
                                (key, through.isoformat(), json.dumps([str(d) for d in days])))
                self.calendar_memory[key] = days
            result.extend(d for d in self.calendar_memory[key] if start <= d <= end)
        return sorted(set(result))

    def stock_month(self, market, code, month):
        through = min(end_of_month(month), self.as_of)
        key = (market, code, month.isoformat())
        if key in self.month_memory:
            return self.month_memory[key]
        if not self.refresh and self.covered_market_month(market, month, through):
            history = self.cache.quotes(month, through, market, code)
        else:
            cached = self.cache.db.execute(
                "SELECT through,payload FROM stock_months WHERE market=? AND code=? AND month=?", key).fetchone()
            if cached and cached[0] >= through.isoformat() and not self.refresh:
                history = []
                for record in json.loads(cached[1]):
                    record["date"] = date.fromisoformat(record["date"])
                    history.append(Quote(**record))
            else:
                if self.offline:
                    recorded = getattr(self.source, "history_failure", lambda *a: None)(market, code, month)
                    if recorded:
                        raise DataError(recorded)
                    raise DataError(f"Stock history missing: {market} {code} {month}")
                history = self.source.monthly(market, code, month, self.as_of)
                if (not history or any(q.market != market or q.code != code
                        or q.date.replace(day=1) != month or q.date > self.as_of for q in history)
                        or len({q.date for q in history}) != len(history)):
                    raise DataError("Mismatched/duplicate/future monthly history")
                self.reserve()
                with self.cache.db:
                    self.cache.db.execute("INSERT OR REPLACE INTO stock_months VALUES (?,?,?,?,?)",
                        (*key, through.isoformat(), json.dumps([q.record() for q in history],
                                                             ensure_ascii=False, allow_nan=False)))
        self.month_memory[key] = [q for q in history if q.date <= self.as_of]
        return self.month_memory[key]

    def start(self, market, code, frame):
        key = {"daily": "daily_months", "weekly": "weekly_months", "monthly": "months"}[frame]
        return max(months_before(self.as_of, self.config["history"][key]),
                   self.securities[(market, code)].listed_on)

    def stage(self, market, code, frame):
        start = self.start(market, code, frame)
        LOG.info("stage_history market=%s code=%s frame=%s start=%s cutoff=%s",
                 market, code, frame, start, self.as_of)
        expected = self.calendar(start, self.as_of)
        history = [q for m in months(start, self.as_of) for q in self.stock_month(market, code, m)
                   if start <= q.date <= self.as_of]
        return self.price_fallback.complete(history), expected

    def previous_quote(self, market, code, previous):
        # Called only after all three KD stages passed. Exact volume from daily
        # reports, never rounded TPEx historical monthly trading lots.
        if previous not in self.precise_days:
            for m in MARKETS:
                self.cache.ensure(self.source, m, previous, self.offline, self.refresh)
            self.precise_days.add(previous)
        if self.cache.day_status(previous) != "open":
            raise DataError("Previous official calendar session has no daily market report")
        rows = self.cache.quotes(previous, previous, market, code)
        if len(rows) != 1:
            raise DataError("Previous market-session quote missing or duplicate")
        return rows[0]


class CheckedDemandHistory(DemandHistory):
    """Same exclusion criteria, checked before downloading unrelated months.

    Only complete cached months are considered evidence of missing sessions.
    Incomplete coverage is a fetch requirement, never a fabricated gap.
    Used by the managed workflow; ordinary screen remains compatible.
    """
    def reserve(self):
        try:
            super().reserve()
        except DataError as exc:
            raise RuntimeError(f"必要行情快取無法安全保存：{exc}") from exc

    def previous_quote(self, market, code, previous):
        self.reserve()
        return super().previous_quote(market, code, previous)

    def _known_month(self, market, code, month):
        through = min(end_of_month(month), self.as_of)
        if not self.refresh and self.covered_market_month(market, month, through):
            return self.cache.quotes(month, through, market, code)
        row = self.cache.db.execute(
            "SELECT through,payload FROM stock_months WHERE market=? AND code=? AND month=?",
            (market, code, str(month))).fetchone()
        if row and row[0] >= str(through) and not self.refresh:
            return [Quote(**{**q, "date": date.fromisoformat(q["date"])}) for q in json.loads(row[1])
                    if q["date"] <= str(self.as_of)]
        return None

    def _check_month(self, rows, expected):
        observed = {q.date for q in rows}
        if len(observed) != len(rows):
            raise DataError("Duplicate history dates")
        missing = set(expected) - observed
        if missing:
            raise DataError(f"History missing {len(missing)} market sessions")
        rows = self.price_fallback.complete(rows)
        if any(not q.valid_prices() for q in rows):
            raise DataError("Invalid/missing unadjusted OHLC")
        return rows

    def stage(self, market, code, frame):
        start = self.start(market, code, frame)
        LOG.info("stage_history market=%s code=%s frame=%s start=%s cutoff=%s",
                 market, code, frame, start, self.as_of)
        expected = self.calendar(start, self.as_of)
        by_month = {}
        for day in expected:
            by_month.setdefault(day.replace(day=1), []).append(day)
        month_list = list(months(start, self.as_of))
        # Known invalid data can rule this stock out before new HTTP requests.
        for month in month_list:
            known = self._known_month(market, code, month)
            if known is not None:
                self._check_month([q for q in known if start <= q.date <= self.as_of],
                                  by_month.get(month, []))
        history = []
        for month in month_list:
            rows = [q for q in self.stock_month(market, code, month) if start <= q.date <= self.as_of]
            history.extend(self._check_month(rows, by_month.get(month, [])))
        return history, expected

