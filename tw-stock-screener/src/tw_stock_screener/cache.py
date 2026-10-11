"""Transactional, incremental daily snapshots; failures are never cached as closed days."""
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
import json
import sqlite3
import shutil
from .models import Quote, DataError, NoData

MARKETS = ("TWSE", "TPEX")


class Cache:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.directory = path.parent
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS days (
            market TEXT, day TEXT, status TEXT NOT NULL,
            PRIMARY KEY(market, day));
          CREATE TABLE IF NOT EXISTS quotes (
            market TEXT, code TEXT, day TEXT, payload TEXT NOT NULL,
            PRIMARY KEY(market,code,day));
          CREATE INDEX IF NOT EXISTS quotes_day ON quotes(day,market);
        """)

    def close(self):
        self.db.close()

    def status(self, market, day):
        row = self.db.execute("SELECT status FROM days WHERE market=? AND day=?",
                              (market, day.isoformat())).fetchone()
        return row[0] if row else None

    def save(self, market, day, quotes):
        if not quotes or any(q.market != market or q.date != day for q in quotes):
            raise DataError("Cannot commit empty/mismatched daily snapshot")
        if len({q.code for q in quotes}) != len(quotes):
            raise DataError("Cannot commit duplicate quotes")
        if shutil.disk_usage(self.directory).free < 1024 ** 3:
            raise DataError("Less than 1 GiB free disk reserve; free space before resuming")
        with self.db:
            self.db.execute("DELETE FROM quotes WHERE market=? AND day=?", (market, day.isoformat()))
            self.db.executemany("INSERT INTO quotes VALUES (?,?,?,?)", [
                (market, q.code, day.isoformat(), json.dumps(q.record(), ensure_ascii=False, allow_nan=False))
                for q in quotes])
            self.db.execute("INSERT OR REPLACE INTO days VALUES (?,?,?)", (market, day.isoformat(), "open"))

    def ensure(self, source, market, day, offline=False, refresh=False):
        status = self.status(market, day)
        recent = datetime.now(timezone(timedelta(hours=8))).date() - timedelta(days=7)
        # A recent 'no data' response can mean a report has not been published yet.
        recheck_closed = status == "closed" and day >= recent and not offline
        if status and not refresh and not recheck_closed:
            return
        if offline:
            recorded = getattr(source, "daily_failure", lambda *a: None)(market, day)
            if recorded:
                raise DataError(recorded)
            raise DataError(f"Cache missing {market} {day}")
        try:
            self.save(market, day, source.daily(market, day))
        except NoData:
            with self.db:
                self.db.execute("DELETE FROM quotes WHERE market=? AND day=?", (market, day.isoformat()))
                self.db.execute("INSERT OR REPLACE INTO days VALUES (?,?,?)",
                                (market, day.isoformat(), "closed"))

    def day_status(self, day):
        statuses = [self.status(m, day) for m in MARKETS]
        if None in statuses:
            raise DataError(f"Incomplete market date: {day}")
        if len(set(statuses)) != 1:
            raise DataError(f"Markets disagree on trading date {day}: {statuses}")
        return statuses[0]

    def sync(self, source, start, end, offline=False, refresh=False, progress=None):
        if start > end:
            raise DataError("start must not exceed end")
        if start < date(2010, 1, 4):
            raise DataError("This adapter supports full-market history from 2010-01-04; "
                            "older dates require separately verified official archives")
        day = start
        while day <= end:
            # Query even weekends: official historical exceptional sessions must not be assumed away.
            for market in MARKETS:
                self.ensure(source, market, day, offline, refresh)
            self.day_status(day)
            if progress:
                progress(day)
            day += timedelta(days=1)

    def calendar(self, start, end):
        result = []
        day = start
        while day <= end:
            if self.day_status(day) == "open":
                result.append(day)
            day += timedelta(days=1)
        return result

    def quotes(self, start, end, market=None, code=None):
        sql = "SELECT payload FROM quotes WHERE day>=? AND day<=?"
        params = [start.isoformat(), end.isoformat()]
        if market:
            sql += " AND market=?"
            params.append(market)
        if code:
            sql += " AND code=?"
            params.append(code)
        rows = self.db.execute(sql + " ORDER BY day,market,code", params)
        result = []
        for (payload,) in rows:
            item = json.loads(payload)
            item["date"] = date.fromisoformat(item["date"])
            result.append(Quote(**item))
        return result


def months_before(day, months):
    # Start on a month boundary: no truncated first monthly candle.
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    return date(year, month + 1, 1)


def resolve_date(cache, source, requested, search_days=20, offline=False, exact=False):
    for offset in range(1 if exact else search_days):
        day = requested - timedelta(days=offset)
        for market in MARKETS:
            cache.ensure(source, market, day, offline)
        if cache.day_status(day) == "open":
            return day
        if exact:
            raise DataError(f"Requested date is not a trading day: {day}")
    raise DataError("No common latest trading day within search window")
