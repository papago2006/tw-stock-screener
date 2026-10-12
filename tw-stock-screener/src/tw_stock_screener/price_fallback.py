"""Fill missing historical OHLC from same-day official daily reports only.

Monthly snapshots remain untouched. Daily snapshots and immutable result
provenance retain the alternative data; no synthetic candle is produced.
"""
from dataclasses import replace
import hashlib
import json
import logging
import math

from .models import DataError
from .sources import daily_url

FIELDS = ("open", "high", "low", "close")
LOG = logging.getLogger(__name__)


def policy(config):
    value = config.get("data_quality", {}).get("ohlc_fallback", "official_daily")
    if value not in ("none", "official_daily"):
        raise DataError("data_quality.ohlc_fallback must be none or official_daily")
    return value


def prices(quote):
    return {key: getattr(quote, key) for key in FIELDS}


def usable(value):
    return value is not None and math.isfinite(value) and value > 0


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode("utf-8")).hexdigest()


class OfficialPriceFallback:
    def __init__(self, cache, source, as_of, config, offline=False, refresh=False, reserve=None):
        self.cache, self.source, self.as_of = cache, source, as_of
        self.policy = policy(config)
        self.offline, self.refresh, self.reserve = offline, refresh, reserve
        self.days, self.memo, self.records = {}, {}, []

    def resolve(self, original):
        if original.date > self.as_of:
            raise DataError("Future prices prohibited in official OHLC fallback")
        if self.policy == "none" or original.valid_prices():
            return original
        # A complete but contradictory candle is a data error, not a missing
        # field. Never silently replace already-present, positive prices.
        if all(usable(getattr(original, key)) for key in FIELDS):
            return original
        identity = (original.market, original.code, original.date)
        memo_key = (*identity, tuple(getattr(original, key) for key in FIELDS))
        if memo_key in self.memo:
            return self.memo[memo_key]
        record = {"market": original.market, "code": original.code, "date": str(original.date),
                  "source_url": daily_url(original.market, original.date),
                  "price_basis": "unadjusted", "original_ohlc": prices(original),
                  "status": "unresolved", "replacement_ohlc": None}
        day_key = original.market, original.date
        if day_key not in self.days:
            try:
                if not self.offline and self.reserve:
                    self.reserve()
                self.cache.ensure(self.source, *day_key, offline=self.offline, refresh=self.refresh)
                self.days[day_key] = None
            except DataError as exc:
                if "disk reserve" in str(exc):
                    raise RuntimeError("Necessary OHLC fallback cache cannot be saved") from exc
                self.days[day_key] = str(exc)
        error = self.days[day_key]
        result = original
        if error:
            record.update(status="source_error", reason=error)
        else:
            rows = self.cache.quotes(original.date, original.date, original.market, original.code)
            if (len(rows) != 1 or any((q.market, q.code, q.date) != identity for q in rows)):
                record["reason"] = "Same-stock/day official daily quote missing or mismatched"
            else:
                alternative = rows[0]
                record["daily_quote_sha256"] = digest(alternative.record())
                if not alternative.valid_prices():
                    record["reason"] = "Official daily report also has invalid/missing OHLC"
                elif any(usable(getattr(original, key)) and
                         getattr(original, key) != getattr(alternative, key) for key in FIELDS):
                    record.update(status="conflict", reason="Existing OHLC disagrees with official daily report")
                else:
                    result = replace(original, **prices(alternative))
                    record.update(status="filled", replacement_ohlc=prices(result))
        self.memo[memo_key] = result
        self.records.append(record)
        LOG.info("ohlc_fallback market=%s code=%s day=%s status=%s",
                 *identity, record["status"])
        return result

    def complete(self, rows):
        result = []
        for index, row in enumerate(rows):
            resolved = self.resolve(row)
            result.append(resolved)
            if not resolved.valid_prices():
                # This stage cannot pass. Do not fetch other missing dates
                # merely to discover more reasons for the same rejection.
                return result + rows[index + 1:]
        return result

    def report(self):
        return {"ohlc_fallback": self.policy, "attempted": len(self.records),
                "filled": sum(r["status"] == "filled" for r in self.records),
                "unresolved": sum(r["status"] != "filled" for r in self.records),
                "records": list(self.records)}
