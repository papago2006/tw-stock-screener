"""Traditional Chinese local summaries; independent of computation and I/O sources."""
from collections import Counter
from pathlib import Path


def explain(reason):
    text = str(reason or "全部條件通過")
    for original, translated in {
        "daily_kd": "日KD未通過", "weekly_kd": "週KD未通過", "monthly_kd": "月KD未通過",
        "volume_ratio": "量比未達門檻", "Invalid/missing unadjusted OHLC": "未還原開高低收價格缺漏或無效",
        "Insufficient warmup": "暖機歷史不足", "History missing": "歷史缺少", "market sessions": "個市場交易日",
        "Stock history missing": "個股月份尚未取得", "Official API failed": "官方資料請求失敗",
        "Previous market-session volume missing or zero": "前一市場交易日成交量缺漏或為零",
        "History does not reach configured warmup start/target": "歷史未涵蓋所需起點或選股日",
        "Historical target OHLC differs from official daily snapshot": "選股日歷史價格與官方日報不一致",
    }.items():
        text = text.replace(original, translated)
    return text


def outcome_counts(report):
    errors = {(e["market"], e["code"]) for e in report["errors"] if e["stage"] != "ranking"}
    selected = {(r["market"], r["code"]) for r in report["results"]}
    return {"processed": len(report["candidates"]), "selected": len(selected), "data_insufficient": len(errors),
            "condition_rejected": len(report["candidates"])-len(selected)-len(errors),
            "ranking_errors": sum(e["stage"] == "ranking" for e in report["errors"])}


def render_summary(report, verification, diagnosis, version=None):
    counts = outcome_counts(report)
    errors = {(e["market"], e["code"]): e for e in report["errors"] if e["stage"] != "ranking"}
    selected = {(r["market"], r["code"]) for r in report["results"]}
    lines = ["# 台股KD選股：本機試用版", "", f"選股日：{report['as_of']}；前一交易日：{report['previous_session']}", "",
             f"狀態：{'僅供樣本驗證' if report.get('report_scope') == 'validation_sample' else '待使用者確認，尚非正式結果'}",
             f"版本：{version or '無正式每日版本'}", "",
             "| 已處理 | 入選 | 條件不符 | 資料不足 | 排名前資料問題 |", "| ---: | ---: | ---: | ---: | ---: |",
             f"| {counts['processed']} | {counts['selected']} | {counts['condition_rejected']} | {counts['data_insufficient']} | {counts['ranking_errors']} |", "",
             f"離線核對：{'一致' if verification.get('matched') else '未通過'}；外部看盤軟體KD：尚未驗收。", "",
             "分類名單日期："+str(report.get("universe", {}).get("as_of", "未知")),
             ("分類採當前名單，非同日分類名單的嚴格歷史回測。" if report.get("universe", {}).get("mode") == "current_universe"
              else "分類及資料基準詳見JSON報告。"), "",
             "資料不足與條件不符分開列出。排名資料不足者不能視為已完整參與排名。", "",
             "| 排名 | 市場 | 代號 | 名稱 | 漲幅% | 量比 | 處理結果 | 原因／階段 |",
             "| ---: | --- | --- | --- | ---: | ---: | --- | --- |"]
    for row in report["candidates"]:
        key = row["market"], row["code"]
        status = "入選" if key in selected else "資料不足" if key in errors else "條件不符"
        reason = explain(errors[key]["reason"] if key in errors else row.get("rejected_at"))
        safe = lambda x: str(x).replace("|", "／").replace("\n", " ").replace("\r", " ")
        ratio = "—" if row.get("volume_ratio") is None else f"{row['volume_ratio']:.4f}"
        lines.append(f"| {row['rank']} | {row['market']} | {safe(row['code'])} | {safe(row['name'])} | {row['change_pct']:.4f} | {ratio} | {status} | {safe(reason)} |")
    lines += ["", "## 入選指標", "", "| 代號 | 日K／D | 週K／D | 月K／D | 量比 |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for row in report["results"]:
        values = [f"{row[f+'_k']:.4f}／{row[f+'_d']:.4f}" for f in ("daily", "weekly", "monthly")]
        lines.append(f"| {row['code']} | {' | '.join(values)} | {row['volume_ratio']:.4f} |")
    lines += ["", "資料診斷請讀同資料夾的diagnostics.json；完整K/D及來源雜湊請讀JSON報告。",
              "價格未還原，包含當期未完成週月棒；缺資料不補價、不放寬暖機。", ""]
    return "\n".join(lines)


def write_summary(path, report, verification, diagnosis, version=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_summary(report, verification, diagnosis, version), encoding="utf-8")
    return path
