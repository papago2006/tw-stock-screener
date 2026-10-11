from datetime import date
import json
from pathlib import Path
from .models import Security, DataError


def load_universe(path, as_of, latest=False):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    snapshot = date.fromisoformat(doc["as_of"])
    if snapshot != as_of and not latest:
        raise DataError("Historical runs require a universe snapshot dated exactly as_of; "
                        "current ISIN must not be silently used for backtests")
    if not doc.get("source_urls") or any(
            not url.startswith("https://isin.twse.com.tw/") for url in doc["source_urls"]):
        raise DataError("Universe must retain official ISIN source URLs")
    if latest and snapshot < as_of:
        raise DataError("Latest universe snapshot predates the selected session")
    securities, seen = [], set()
    for row in doc["securities"]:
        s = Security(**{**row, "listed_on": date.fromisoformat(row["listed_on"])})
        key = (s.market, s.code)
        if key in seen or s.market not in {"TWSE", "TPEX"}:
            raise DataError("Invalid/duplicate universe entry")
        seen.add(key)
        if s.ordinary and s.listed_on <= as_of:
            securities.append(s)
    if {s.market for s in securities} != {"TWSE", "TPEX"}:
        raise DataError("Universe must contain ordinary stocks in both markets")
    return securities, {"as_of": snapshot.isoformat(), "source_urls": doc["source_urls"],
                        "mode": "current_universe" if snapshot != as_of else "point_in_time",
                        "warning": "Current classification can omit recently delisted/transferred securities"
                        if snapshot != as_of else None}
