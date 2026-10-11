"""Offline data-quality evidence. Never changes prices or infers suspensions."""
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import gzip
import hashlib
import json

from .demand import DemandHistory, months
from .models import DataError
from .sources import parse_monthly, parse_daily, raw_response_path


class RawEvidence:
    def __init__(self, raw_dir):
        self.directory = Path(raw_dir)
        self.months, self.days, self.memo = {}, {}, {}
        manifest = self.directory / "requests.jsonl"
        if manifest.exists():
            with manifest.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                        parsed = urlparse(row["url"])
                        if parsed.scheme != "https" or parsed.hostname not in {"www.twse.com.tw", "www.tpex.org.tw"}:
                            continue
                        query = parse_qs(parsed.query)
                        stamp = query.get("date", [""])[0].replace("/", "")
                        if len(stamp) != 8:
                            continue
                        ds = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}"
                        if parsed.path.endswith("/STOCK_DAY"):
                            self.months[("TWSE", query["stockNo"][0], ds)] = row
                        elif parsed.path.endswith("/tradingStock"):
                            self.months[("TPEX", query["code"][0], ds)] = row
                        elif parsed.path.endswith("/MI_INDEX"):
                            self.days[("TWSE", ds)] = row
                        elif parsed.path.endswith("/dailyQuotes"):
                            self.days[("TPEX", ds)] = row
                    except (ValueError, KeyError, TypeError):
                        continue

    def prices(self, market, code, day, as_of, daily=False):
        stamp = str(day if daily else day.replace(day=1))
        key = market, code, stamp, daily, str(as_of)
        if key in self.memo:
            return self.memo[key]
        entry = (self.days.get((market, stamp)) if daily else self.months.get((market, code, stamp)))
        evidence = {"verified": False, "kind": "daily" if daily else "monthly"}
        rows = None
        try:
            if not entry:
                raise DataError("沒有可定位的原始回應紀錄")
            path = raw_response_path(self.directory, entry["sha256"])
            content = gzip.decompress(path.read_bytes())
            if hashlib.sha256(content).hexdigest() != entry["sha256"]:
                raise DataError("原始回應雜湊不符")
            doc = json.loads(content)
            parsed = (parse_daily(market, day, doc) if daily else
                      parse_monthly(market, code, day.replace(day=1), as_of, doc))
            rows = {str(q.date): [q.open, q.high, q.low, q.close] for q in parsed if q.code == code}
            evidence.update(verified=True, url=entry["url"], sha256=entry["sha256"], path=str(path))
        except (OSError, ValueError, KeyError, TypeError, EOFError) as exc:
            evidence["reason"] = str(exc)
        self.memo[key] = rows, evidence
        return rows, evidence

    def check(self, market, code, day, as_of, prices=None, absent=False):
        attempts = []
        for daily in (False, True):
            rows, evidence = self.prices(market, code, day, as_of, daily)
            evidence = dict(evidence)
            attempts.append(evidence)
            if rows is None:
                continue
            observed = rows.get(str(day))
            if absent and observed is None:
                evidence["finding"] = "official_response_omits_session"
                return evidence
            if not absent and observed == prices:
                evidence["finding"] = "official_response_confirms_cached_prices"
                return evidence
            evidence.update(finding="cached_and_official_response_differ", official_prices=observed)
        return {"verified": False, "finding": "unresolved", "attempts": attempts}


def diagnose_report(report, cache, securities, cfg, evidence=None):
    """Inspect complete cached months and list exact missing/invalid dates.

    Uncached months are explicitly unknown, never treated as confirmed gaps.
    No source is provided: this routine cannot perform HTTP or backfill.
    """
    as_of = date.fromisoformat(report["as_of"])
    provider = DemandHistory(cache, None, as_of, securities, cfg, offline=True)
    records = []
    for error in report["errors"]:
        if error["stage"] == "ranking":
            continue
        key = error["market"], error["code"]
        frame = error["stage"].removesuffix("_kd")
        item = {**error, "invalid_prices": [], "missing_sessions": [], "unavailable_months": [],
                "cause_of_nontrading": "not_inferred"}
        records.append(item)
        if frame not in {"daily", "weekly", "monthly"}:
            item["classification"] = "exact_volume_or_other_data_error"
            continue
        start = provider.start(*key, frame)
        item["required_start"] = str(start)
        try:
            expected = provider.calendar(start, as_of)
        except DataError as exc:
            item.update(classification="calendar_unavailable", detail=str(exc))
            continue
        history = []
        for month in months(start, as_of):
            try:
                rows = [q for q in provider.stock_month(*key, month) if start <= q.date <= as_of]
            except DataError as exc:
                item["unavailable_months"].append({"month": str(month), "reason": str(exc)})
                continue
            history.extend(rows)
            observed = {q.date for q in rows}
            for day in sorted(d for d in expected if d.replace(day=1) == month and d not in observed):
                detail = {"date": str(day)}
                if evidence:
                    detail["evidence"] = evidence.check(*key, day, as_of, absent=True)
                item["missing_sessions"].append(detail)
            for q in rows:
                if not q.valid_prices():
                    prices = [q.open, q.high, q.low, q.close]
                    detail = {"date": str(q.date), "ohlc": prices}
                    if evidence:
                        detail["evidence"] = evidence.check(*key, q.date, as_of, prices=prices)
                    item["invalid_prices"].append(detail)
        groups = lambda days: {d if frame == "daily" else d.isocalendar()[:2] if frame == "weekly"
                               else (d.year, d.month) for d in days}
        item.update(observed_bars=len(groups(q.date for q in history)),
                    maximum_calendar_bars=len(groups(expected)),
                    required_bars=cfg["kd"]["period"]+cfg["kd"]["warmup_periods"])
        findings = item["invalid_prices"]+item["missing_sessions"]
        item["verified_problem_dates"] = sum(d.get("evidence", {}).get("verified", False) for d in findings)
        item["unverified_problem_dates"] = len(findings)-item["verified_problem_dates"]
        item["classification"] = ("warmup_insufficient" if "Insufficient warmup" in error["reason"] else
                                  "cached_data_quality_problem" if findings else "unresolved_data_error")
    ranking = [e for e in report["errors"] if e["stage"] == "ranking"]
    return {"as_of": str(as_of), "scope": report.get("report_scope", "full"),
            "sample_or_candidate_data_errors": len(records), "records": records,
            "ranking_errors": ranking, "ranking_error_count": len(ranking),
            "external_kd_software_verified": False,
            "note": "原始回應驗證只確認資料內容或缺列，不推論停牌原因，不補價或更改選股條件"}
