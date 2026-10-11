"""Export a verified saved result to a static, public, read-only report.

This presentation adapter does not fetch quotes, recalculate indicators, or approve
versions. Its explicit field list prevents local paths/request logs being published.
"""
import argparse
from contextlib import closing
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import sqlite3

from .presentation import outcome_counts
from .results import validate_report


STOCK_FIELDS = ("rank", "market", "code", "name", "change_pct", "volume", "volume_ratio",
                "daily_k", "daily_d", "weekly_k", "weekly_d", "monthly_k", "monthly_d",
                "daily_history_start", "weekly_history_start", "monthly_history_start")
REASONS = {
    "daily_kd": "日 KD 未通過", "weekly_kd": "週 KD 未通過", "monthly_kd": "月 KD 未通過",
    "volume_ratio": "量比未達門檻",
}
REPLAY_CHECKS = {"as_of", "previous_session", "counts", "results", "candidates", "errors", "input_hashes", "no_http_during_replay"}


def public_error(reason):
    """Use categories, never raw exceptions which may contain private paths/URLs."""
    text = str(reason)
    for original, label in (
        ("Invalid/missing unadjusted OHLC", "未還原開高低收價格缺漏或無效"),
        ("Insufficient warmup", "暖機歷史不足"),
        ("History missing", "歷史缺少市場交易日"),
        ("Previous market-session volume missing or zero", "前一交易日成交量缺漏或為零"),
        ("Unknown official percentage-change reference", "官方漲跌幅參考基準無法辨識"),
        ("Missing/duplicate target quote", "當日行情缺漏或重複"),
        ("Historical target OHLC differs", "歷史價格與當日日報不一致"),
        ("History does not reach", "歷史未涵蓋所需日期"),
    ):
        if original in text:
            return label
    return "資料不足或來源失敗；詳細證據保留於本機報告"


def read_version(database, version):
    """Read-only SQLite access, with the same content hash as ResultStore."""
    path = Path(database).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM versions WHERE id=?", (version,)).fetchone()
        if row is None:
            raise ValueError("找不到指定結果版本")
        if hashlib.sha256(row["report"].encode("utf-8")).hexdigest() != row["sha256"]:
            raise ValueError("結果版本內容雜湊不符")
        current = db.execute("SELECT version FROM official WHERE day=?", (row["day"],)).fetchone()
        return {"version": row["id"], "sha256": row["sha256"], "created_at": row["created_at"],
                "official": bool(current and current[0] == version), "report": json.loads(row["report"])}


def make_public_report(record, summary):
    report = record["report"]
    validate_report(report)
    verification = summary.get("verification", {})
    if (summary.get("version") != record["version"] or summary.get("as_of") != report["as_of"]
            or summary.get("job_id") != report.get("job", {}).get("id")
            or summary.get("status") not in {"complete", "complete_with_exclusions"}
            or verification.get("matched") is not True
            or not REPLAY_CHECKS.issubset(verification.get("checks", {}))
            or not all(v is True for v in verification["checks"].values())
            or verification["checks"].get("no_http_during_replay") is not True):
        raise ValueError("需要與指定版本相符、已通過離線核對的完整工作摘要")
    counts = outcome_counts(report)
    if counts != summary["outcomes"] or report["counts"] != summary["counts"]:
        raise ValueError("工作摘要數量與結果版本不一致")
    if counts["processed"] != report["counts"]["top_n"]:
        raise ValueError("候選尚未全部處理，不能匯出為完整報表")
    selected = {(r["market"], r["code"]) for r in report["results"]}
    errors = {(e["market"], e["code"]): e for e in report["errors"] if e["stage"] != "ranking"}
    candidates = []
    for row in report["candidates"]:
        key = (row["market"], row["code"])
        item = {field: row.get(field) for field in STOCK_FIELDS}
        if key in selected and key in errors:
            raise ValueError("資料錯誤股票不可同時列為入選")
        item["outcome"] = "selected" if key in selected else "data_insufficient" if key in errors else "condition_rejected"
        item["reason"] = ("全部條件通過" if key in selected else public_error(errors[key]["reason"])
                          if key in errors else REASONS.get(row.get("rejected_at"), "選股條件未通過"))
        candidates.append(item)
    if len({(r["market"], r["code"]) for r in candidates}) != len(candidates):
        raise ValueError("候選股票重複")
    if not selected.issubset({(r["market"], r["code"]) for r in candidates}):
        raise ValueError("入選股票不在候選內")
    # Parameters are copied from this saved calculation, never today's YAML.
    config = report["config"]
    return {
        "schema_version": 1, "as_of": report["as_of"], "previous_session": report["previous_session"],
        "generated_at": report["generated_at"], "exported_at": datetime.now(timezone.utc).isoformat(),
        "version": record["version"], "report_sha256": record["sha256"],
        "publication_status": "official" if record["official"] else "pending",
        "offline_verified": True, "external_kd_software_verified": False,
        "universe": {k: report.get("universe", {}).get(k) for k in ("as_of", "mode")},
        "counts": report["counts"], "outcomes": counts,
        "rules": {"selection": {k: config["selection"][k] for k in ("top_n", "volume_ratio")},
                  "kd": {k: config["kd"][k] for k in ("period", "k_smoothing", "d_smoothing", "initial", "warmup_periods")},
                  "history": {k: config["history"][k] for k in ("months", "daily_months", "weekly_months")}},
        "candidates": candidates,
        "ranking_errors": [{"market": e["market"], "code": e["code"], "reason": public_error(e["reason"])}
                           for e in report["errors"] if e["stage"] == "ranking"],
        "tracking": {"status": "unavailable", "reason": "此頁尚未接入正式跨日追蹤，不顯示新增、連續或退出訊號"},
    }


def export_site(record, summary, assets, output):
    data = make_public_report(record, summary)
    assets, output = Path(assets).resolve(strict=True), Path(output).resolve()
    if assets == output:
        raise ValueError("靜態原始檔與輸出目錄必須分開")
    # Do not overwrite a previous different result on export.
    existing = output / "data.json"
    if existing.exists():
        previous = json.loads(existing.read_text(encoding="utf-8"))
        if (previous["version"], previous["report_sha256"]) != (data["version"], data["report_sha256"]):
            raise ValueError("輸出目錄已有不同版本，請使用新目錄保留舊版")
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2)
    output.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "styles.css", "app.js", "icon.svg", "manifest.webmanifest"):
        shutil.copy2(assets / name, output / name)
    (output / "data.json").write_text(payload + "\n", encoding="utf-8")
    # Also works from file://, and escapes the JS/HTML boundaries.
    escaped = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    (output / "data.js").write_text("window.SCREENER_REPORT = " + escaped + ";\n", encoding="utf-8")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(STOCK_FIELDS[:13])
    for row in data["candidates"]:
        if row["outcome"] == "selected":
            values = [row.get(k) for k in STOCK_FIELDS[:13]]
            # Spreadsheet formula injection protection; full values remain in JSON.
            writer.writerow(["'" + v if isinstance(v, str) and re.match(r"^[=+@\-\t\r]", v) else v for v in values])
    (output / "selected.csv").write_text(stream.getvalue(), encoding="utf-8-sig")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    return data


def main():
    parser = argparse.ArgumentParser(description="只讀匯出已核對版本為手機報表，不下載、不批准、不部署")
    parser.add_argument("--results-db", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--assets", type=Path, default=Path("web"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    record = read_version(args.results_db, args.version)
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    data = export_site(record, summary, args.assets, args.output)
    print(json.dumps({"as_of": data["as_of"], "publication_status": data["publication_status"],
                      "outcomes": data["outcomes"], "output": str(args.output)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
