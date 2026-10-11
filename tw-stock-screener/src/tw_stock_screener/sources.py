"""Official website adapters. Transport and parsing deliberately independent of cache."""
from datetime import date
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import URLError
import gzip
import hashlib
import json
import logging
import math
import os
import re
import ssl
import shutil
import time
import tempfile
from typing import Protocol
from .models import Quote, Security, DataError, NoData

LOG = logging.getLogger(__name__)
URLS = {"TWSE": "https://www.twse.com.tw/exchangeReport/MI_INDEX",
        "TPEX": "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"}


def open_verified(request, timeout):
    try:
        return urlopen(request, timeout=timeout)
    except URLError as exc:
        reason = exc.reason
        if (not isinstance(reason, ssl.SSLCertVerificationError)
                or "Missing Subject Key Identifier" not in str(reason)):
            raise
        # Python 3.13 enabled STRICT by default. Permit the older certificate
        # format only for this specific error, retaining CA and hostname checks.
        context = ssl.create_default_context()
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        LOG.warning("TLS certificate format compatibility retry; CA/hostname verification enabled")
        return urlopen(request, timeout=timeout, context=context)


class Source(Protocol):
    def daily(self, market: str, day: date) -> list[Quote]: ...
    def monthly(self, market: str, code: str, month: date, as_of: date) -> list[Quote]: ...
    def trading_month(self, month: date, as_of: date) -> list[date]: ...


def raw_response_path(directory, digest):
    """Read legacy flat archives; put new ones under one short root entry.

    FAT32 can exhaust directory entries despite free disk space. The extra
    'v2' level lets an already-full legacy folder add just one short directory.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise DataError("Invalid raw response SHA-256")
    directory = Path(directory)
    legacy = directory / f"{digest}.gz"
    return legacy if legacy.is_file() else directory / "v2" / digest[:2] / f"{digest}.gz"


def number(value):
    s = str(value).strip().replace(",", "")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", s):
        return None
    n = float(s)
    return n if math.isfinite(n) else None


def parse_date(s):
    parts = [int(x) for x in re.split(r"[/\-]", s)]
    if len(parts) != 3:
        raise DataError(f"Invalid date: {s}")
    y, m, d = parts
    return date(y + 1911 if y < 1911 else y, m, d)


def clean(s):
    return unescape(re.sub(r"<[^>]*>", "", str(s))).strip()


def historical_date(value, market, notes):
    # TPEx marks the listing day with '*'. Accept only the documented marker;
    # malformed dates and other footnotes must still fail closed.
    value = str(value).strip()
    if market == "TPEX" and value.endswith("*"):
        if (not re.fullmatch(r"\d{3,4}/\d{2}/\d{2}\*", value)
                or not any("表示該日為該股票上櫃掛牌首日" in str(n) for n in notes)):
            raise DataError("Unverified TPEx historical date annotation")
        value = value[:-1]
    return parse_date(value)


def parse_monthly(market, code, month, as_of, doc):
    """Daily unadjusted prices returned one month at a time. TPEx lots are NOT shares."""
    stamp = month.replace(day=1).strftime("%Y%m%d")
    if not isinstance(doc, dict) or str(doc.get("stat", "")).lower() != "ok":
        raise DataError(f"{market} {code} monthly API: {doc.get('stat') if isinstance(doc, dict) else 'invalid JSON'}")
    if doc.get("date") != stamp:
        raise DataError("Wrong historical month returned")
    if market == "TWSE":
        if not re.search(rf"\b{re.escape(code)}\b", doc.get("title", "")):
            raise DataError("Wrong historical stock returned")
        table, fields = doc, ["日期", "成交股數", "開盤價", "最高價", "最低價", "收盤價", "漲跌價差"]
    else:
        if doc.get("code") != code:
            raise DataError("Wrong historical stock returned")
        tables = doc.get("tables")
        if not isinstance(tables, list) or len(tables) != 1:
            raise DataError("Unexpected TPEx historical tables")
        table = tables[0]
        # Older official months use '成交仟股', newer ones '成交張數'. Neither
        # supplies the exact daily volume used by the final volume-ratio rule.
        volumes = set(table.get("fields", [])) & {"成交張數", "成交仟股"}
        if len(volumes) != 1:
            raise DataError("Unknown/ambiguous TPEx historical volume field")
        fields = ["日 期", volumes.pop(), "開盤", "最高", "最低", "收盤", "漲跌"]
    schema = table.get("fields", [])
    if not set(fields).issubset(schema) or not isinstance(table.get("data"), list):
        raise DataError("Changed monthly historical schema")
    result, seen = [], set()
    for row in table["data"]:
        if not isinstance(row, list) or len(row) != len(schema):
            raise DataError("Malformed historical row")
        r = dict(zip(schema, row))
        day = historical_date(r[fields[0]], market, table.get("notes", []))
        if day.strftime("%Y%m") != month.strftime("%Y%m") or day in seen:
            raise DataError("Duplicate/wrong-month historical date")
        seen.add(day)
        if day > as_of:
            continue
        vol = number(r[fields[1]]) if market == "TWSE" else None
        volume = int(vol) if vol is not None and vol >= 0 and vol.is_integer() else None
        prices = [number(r[n]) for n in fields[2:6]]
        result.append(Quote(day, market, code, doc.get("name", code), *prices,
                            volume, number(r[fields[6]])))
    if not result:
        raise DataError("Empty historical month at requested cutoff")
    return sorted(result, key=lambda q: q.date)


def parse_trading_month(month, as_of, doc):
    if (not isinstance(doc, dict) or str(doc.get("stat", "")).lower() != "ok"
            or doc.get("date") != month.replace(day=1).strftime("%Y%m%d")):
        raise DataError("Invalid official market calendar response")
    fields = doc.get("fields", [])
    if "日期" not in fields or not isinstance(doc.get("data"), list):
        raise DataError("Changed official market calendar schema")
    days = []
    for row in doc["data"]:
        if not isinstance(row, list) or len(row) != len(fields):
            raise DataError("Malformed official calendar row")
        day = parse_date(row[fields.index("日期")])
        if (day.year, day.month) != (month.year, month.month) or day in days:
            raise DataError("Wrong/duplicate calendar date")
        if day <= as_of:
            days.append(day)
    if not days:
        raise DataError("Empty official trading month")
    return sorted(days)


def parse_daily(market, day, payload):
    if not isinstance(payload, dict):
        raise DataError("Expected official JSON object")
    stat = str(payload.get("stat", "")).strip()
    # Do not classify arbitrary API error strings as holidays.
    if stat in {"很抱歉，沒有符合條件的資料!", "很抱歉，沒有符合條件的資料！", "查無資料"}:
        raise NoData(stat)
    if stat.lower() != "ok":
        raise DataError(f"{market} unexpected stat: {stat!r}")
    if payload.get("date") != day.strftime("%Y%m%d"):
        raise DataError(f"{market} returned a different date")
    tables = payload.get("tables")
    if not isinstance(tables, list) or not all(isinstance(t, dict) for t in tables):
        raise DataError("Missing tables")
    codefield = "證券代號" if market == "TWSE" else "代號"
    wanted = [t for t in tables if codefield in t.get("fields", [])]
    if not wanted or (market == "TWSE" and len(wanted) != 1):
        raise DataError("Missing/ambiguous daily quotes table")
    if market == "TPEX":
        if any(t.get("fields") != wanted[0]["fields"] for t in wanted):
            raise DataError("Inconsistent TPEx quote table schemas")
        if (all(t.get("data") == [] and t.get("totalCount") == 0 for t in wanted)
                and wanted[0].get("listedCompanies") == "0"):
            raise NoData("TPEx official zero-company/zero-row daily report")
    if any(not isinstance(t.get("data"), list) for t in wanted):
        raise DataError("Missing table data array")
    table = {**wanted[0], "data": [r for t in wanted for r in t["data"]]}
    fields = table["fields"]
    names = (["證券代號", "證券名稱", "開盤價", "最高價", "最低價", "收盤價", "成交股數", "漲跌價差", "漲跌(+/-)"]
             if market == "TWSE" else ["代號", "名稱", "開盤", "最高", "最低", "收盤", "成交股數", "漲跌"])
    if not set(names).issubset(fields) or len(fields) != len(set(fields)):
        raise DataError("Changed daily quote schema")
    quotes, seen = [], set()
    for row in table.get("data", []):
        if not isinstance(row, list) or len(row) != len(fields):
            raise DataError("Daily quote row length mismatch")
        r = dict(zip(fields, row))
        code, name = clean(r[names[0]]), clean(r[names[1]])
        if not code or code in seen:
            raise DataError("Missing/duplicate security code")
        seen.add(code)
        prices = [number(r[n]) for n in names[2:6]]
        vol = number(r[names[6]])
        volume = int(vol) if vol is not None and vol >= 0 and vol.is_integer() else None
        change = number(r[names[7]])
        if market == "TWSE":
            sign = clean(r[names[8]])
            if sign == "-" and change is not None:
                change = -abs(change)
            elif sign == "+" and change is not None:
                change = abs(change)
            elif sign == "" and change == 0:
                pass
            else:
                change = None  # X, ex-rights/ex-dividend/undefined reference
        quotes.append(Quote(day, market, code, name, *prices, volume, change))
    if not quotes:
        raise DataError("Successful response contained zero quote rows")
    return quotes


class OfficialSource:
    def __init__(self, network, raw_dir):
        self.network = network
        self.raw_dir = Path(raw_dir)
        self.last_request = 0.0

    def fetch(self, url):
        for attempt in range(self.network["attempts"]):
            delay = self.network["interval_seconds"] - (time.monotonic() - self.last_request)
            if delay > 0:
                time.sleep(delay)
            self.last_request = time.monotonic()
            try:
                req = Request(url, headers={"User-Agent": "tw-stock-screener/0.1 (official-public-data)",
                                            "Accept": "application/json,text/html"})
                with open_verified(req, timeout=self.network["timeout"]) as response:
                    content = response.read()
            except (OSError, TimeoutError) as exc:
                LOG.warning("request_failed url=%s attempt=%s error=%s", url, attempt + 1, exc)
                if attempt + 1 == self.network["attempts"]:
                    raise DataError(f"Official API failed: {url}: {exc}") from exc
                time.sleep(min(2 ** attempt, 30))
                continue
            # Local I/O failures are not API failures. Abort without repeating
            # successful HTTP requests or producing a partial success report.
            self.archive(url, content)
            return content

    def archive(self, url, content):
        temporary = None
        try:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(self.raw_dir).free < 1024 ** 3 + len(content) * 2:
                raise DataError("Less than 1 GiB free disk reserve; free space before resuming")
            digest = hashlib.sha256(content).hexdigest()
            path = raw_response_path(self.raw_dir, digest)
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as f:
                    temporary = Path(f.name)
                    f.write(gzip.compress(content))
                os.replace(temporary, path)
                temporary = None
            with (self.raw_dir / "requests.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"url": url, "sha256": digest,
                                    "raw_path": path.relative_to(self.raw_dir).as_posix(),
                                    "fetched_at_unix": time.time()}) + "\n")
        except OSError as exc:
            raise OSError(exc.errno, f"Local raw archive failed; HTTP was not retried: {exc}",
                          exc.filename or str(self.raw_dir)) from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    LOG.warning("Could not remove incomplete raw temporary file: %s", temporary)

    def daily(self, market, day):
        if market not in URLS:
            raise DataError(f"Unknown market: {market}")
        params = ({"response": "json", "date": day.strftime("%Y%m%d"), "type": "ALLBUT0999"}
                  if market == "TWSE" else
                  {"response": "json", "date": day.strftime("%Y/%m/%d"), "id": ""})
        content = self.fetch(URLS[market] + "?" + urlencode(params))
        try:
            return parse_daily(market, day, json.loads(content))
        except (ValueError, TypeError, KeyError) as exc:
            if isinstance(exc, DataError):
                raise
            raise DataError(f"{market} invalid JSON/schema: {exc}") from exc

    def monthly(self, market, code, month, as_of):
        if market == "TWSE":
            url = "https://www.twse.com.tw/exchangeReport/STOCK_DAY?" + urlencode({
                "response": "json", "date": month.strftime("%Y%m01"), "stockNo": code})
        elif market == "TPEX":
            url = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?" + urlencode({
                "response": "json", "date": month.strftime("%Y/%m/01"), "code": code})
        else:
            raise DataError("Unknown history market")
        LOG.info("fetch_history market=%s code=%s month=%s cutoff=%s", market, code, month, as_of)
        try:
            return parse_monthly(market, code, month, as_of, json.loads(self.fetch(url)))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise DataError(f"{market} {code} {month}: {exc}") from exc

    def trading_month(self, month, as_of):
        url = "https://www.twse.com.tw/exchangeReport/FMTQIK?" + urlencode({
            "response": "json", "date": month.strftime("%Y%m01")})
        try:
            return parse_trading_month(month, as_of, json.loads(self.fetch(url)))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise DataError(f"Official market calendar {month}: {exc}") from exc

    def universe(self):
        result, dates, urls = [], [], []
        for market, mode in [("TWSE", 2), ("TPEX", 4)]:
            url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
            raw = self.fetch(url)
            ds, securities = parse_isin(raw, market)
            dates.append(ds)
            urls.append(url)
            result.extend(securities)
        if dates[0] != dates[1]:
            raise DataError("ISIN market snapshots have different update dates")
        return {"as_of": dates[0].isoformat(), "source_urls": urls,
                "securities": [{**s.__dict__, "listed_on": s.listed_on.isoformat()} for s in result]}


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag == "td" and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = self.cell = None


def parse_isin(raw, market):
    text = raw.decode("cp950")
    match = re.search(r"最近更新日期\s*[:：]\s*(\d{4}/\d{2}/\d{2})", text)
    if not match:
        raise DataError("ISIN update date missing")
    parser = TableParser()
    parser.feed(text)
    result = []
    label = "上市" if market == "TWSE" else "上櫃"
    for row in parser.rows:
        if len(row) != 7 or row[3] != label:
            continue
        identity = row[0].split(maxsplit=1)
        if len(identity) == 2 and re.fullmatch(r"[A-Z]{6}", row[5]):
            sec = Security(market, identity[0], identity[1], row[5], parse_date(row[2]))
            if sec.ordinary:
                result.append(sec)
    if not result or len({s.code for s in result}) != len(result):
        raise DataError(f"ISIN {market}: empty/duplicate ordinary stock list")
    return parse_date(match[1]), result
