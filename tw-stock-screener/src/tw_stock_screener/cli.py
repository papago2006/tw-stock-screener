import argparse
from datetime import date, datetime
from pathlib import Path
import json
import logging
import sqlite3
import sys
import time
from datetime import timedelta, timezone
from .cache import Cache
from .config import load_config
from .models import DataError
from .output import export_report, write_json
from .sources import OfficialSource, parse_isin
from .application import ScreenRequest, run_screen
from .verification import verify
from .results import SQLiteResultStore
from .tracking import track_official


def taipei_today():
    return datetime.now(timezone(timedelta(hours=8))).date()


def parser():
    p = argparse.ArgumentParser(description="TWSE/TPEx official daily screener")
    p.add_argument("--config", default="config.yaml")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("work", help="Managed local trial: sample gate, full selection, offline verification, pending report")
    s.add_argument("--date", type=date.fromisoformat)
    s.add_argument("--universe")
    s.add_argument("--offline", action="store_true")
    s.add_argument("--refresh", action="store_true")
    s.add_argument("--recorded-run", help="Existing recovery directory; preserve original cutoff/universe warning")
    s.add_argument("--new-run", action="store_true", help="Create another job/version instead of resuming the same job")
    s = sub.add_parser("work-status", help="Read latest managed-job status; never downloads market data")
    s.add_argument("--json", action="store_true", help="Include machine-readable detailed state")
    s = sub.add_parser("screen", help="Screen latest completed session or exact historical date")
    s.add_argument("--date", type=date.fromisoformat)
    s.add_argument("--universe", help="Official dated ISIN universe snapshot JSON")
    s.add_argument("--offline", action="store_true")
    s.add_argument("--refresh", action="store_true", help="Refetch cached dates (official corrections)")
    s = sub.add_parser("sync", help="Incrementally download full-market daily OHLCV")
    s.add_argument("--start", type=date.fromisoformat, required=True)
    s.add_argument("--end", type=date.fromisoformat, required=True)
    s.add_argument("--refresh", action="store_true")
    s = sub.add_parser("snapshot-universe", help="Capture current official ordinary-stock classification")
    s.add_argument("--output", required=True)
    s = sub.add_parser("import-universe", help="Convert archived official ISIN HTML to dated JSON")
    s.add_argument("--twse-html", required=True)
    s.add_argument("--tpex-html", required=True)
    s.add_argument("--output", required=True)
    s = sub.add_parser("verify-kd", help="Export all RSV/K/D values for independent comparison")
    s.add_argument("--date", type=date.fromisoformat, required=True)
    s.add_argument("--market", choices=["TWSE", "TPEX"], required=True)
    s.add_argument("--code", required=True)
    s.add_argument("--timeframe", choices=["daily", "weekly", "monthly"], default="daily")
    s.add_argument("--output", required=True)
    s.add_argument("--reference", help="External CSV with date,k,d (same seeds/history/RSV convention)")
    s.add_argument("--tolerance", type=float, default=1e-8)
    s = sub.add_parser("versions", help="List saved result versions (no market download)")
    s.add_argument("--date", type=date.fromisoformat)
    s = sub.add_parser("show-result", help="Inspect a version or an official daily result")
    target = s.add_mutually_exclusive_group(required=True)
    target.add_argument("--version")
    target.add_argument("--date", type=date.fromisoformat)
    s.add_argument("--output", help="Optional JSON export path")
    s = sub.add_parser("approve", help="Explicitly select the official version after review")
    s.add_argument("--version", required=True)
    s.add_argument("--expected-current", required=True, help="Reviewed official version ID; 'none' for first approval")
    s.add_argument("--allow-errors", action="store_true", help="Acknowledge errors in the reviewed report")
    s = sub.add_parser("track", help="Compare exact previous-session official results")
    s.add_argument("--date", type=date.fromisoformat, required=True)
    s.add_argument("--output", help="Optional JSON export path")
    return p


def result_command(args, cfg):
    with SQLiteResultStore(cfg["paths"]["results"]) as store:
        if args.command == "versions":
            value = store.versions(str(args.date) if args.date else None)
        elif args.command == "show-result":
            value = store.get(args.version) if args.version else store.official(str(args.date))
            if value is None:
                raise DataError(f"No official result for {args.date}")
        elif args.command == "approve":
            value = store.approve(args.version,
                                  None if args.expected_current == "none" else args.expected_current,
                                  args.allow_errors)
        else:
            value = track_official(store, str(args.date))
    if getattr(args, "output", None):
        write_json(args.output, value)
    print(json.dumps(value, ensure_ascii=False, allow_nan=False))
    return 0


def main(argv=None):
    # Windows redirected output may default to cp1252. Chinese paths must not
    # turn a successfully saved report into a failed run during the final print.
    for stream in (sys.stdout, sys.stderr):
        if callable(getattr(stream, "reconfigure", None)):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = parser().parse_args(argv)
    cache, cfg = None, None
    try:
        cfg = load_config(args.config)
        if args.command == "work-status":
            from .jobs import read_status
            value = read_status(Path(cfg["paths"]["output"]) / "work-current.json")
            labels = {"not_started": "尚未開始", "starting": "準備中", "running": "執行中",
                      "interrupted": "已中斷，可重新執行續跑", "failed": "失敗，請檢查原因",
                      "complete": "已完成，結果待確認", "complete_with_exclusions": "已完成，含資料不足排除；結果待確認"}
            print(labels.get(value["status"], value["status"]))
            phases = {"sample_validation": "前30檔與離線核對", "sample_diagnosis": "前30檔資料診斷",
                      "full_selection": "完整候選選股", "full_offline_verification": "完整離線核對",
                      "full_diagnosis": "完整資料診斷", "saving_pending_result": "保存待確認結果", "finished": "已結束"}
            if value.get("phase"):
                print("階段：" + phases.get(value["phase"], value["phase"]))
            if value.get("candidate"):
                print(f"已處理：{value.get('processed', 0)}／{value.get('total', 0)}；目前：{value['candidate']}")
            if "network_calls" in value:
                print(f"必要官方查詢：{value['network_calls']}；直接重用原始回應：{value.get('archive_hits', 0)}")
            if value.get("error"):
                print("原因：" + value["error"])
            if value.get("outcomes"):
                counts = value["outcomes"]
                print(f"已處理{counts['processed']}；入選{counts['selected']}；條件不符{counts['condition_rejected']}；資料不足{counts['data_insufficient']}")
            if value.get("job_directory"):
                print("工作資料夾：" + value["job_directory"])
            if args.json:
                print(json.dumps(value, ensure_ascii=False))
            return 0
        output = Path(cfg["paths"]["output"])
        output.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                            handlers=[logging.StreamHandler(), logging.FileHandler(output / "run.log", encoding="utf-8")],
                            force=True)
        if args.command in {"versions", "show-result", "approve", "track"}:
            return result_command(args, cfg)
        if args.command == "work":
            from .workflow import run_workflow
            summary = run_workflow(cfg, ScreenRequest(args.date, args.universe, args.offline, args.refresh),
                                   taipei_today(), args.recorded_run, args.new_run)
            print("工作完成；選股結果待確認。中文報告：" + summary["files"][2])
            print(json.dumps(summary, ensure_ascii=False))
            return 0
        source = OfficialSource(cfg["network"], cfg["paths"]["raw"])
        today = taipei_today()
        if args.command == "snapshot-universe":
            doc = source.universe()
            write_json(args.output, doc)
            print(f"Saved {len(doc['securities'])} ordinary stocks, dated {doc['as_of']}")
            return 0
        if args.command == "import-universe":
            ds1, s1 = parse_isin(Path(args.twse_html).read_bytes(), "TWSE")
            ds2, s2 = parse_isin(Path(args.tpex_html).read_bytes(), "TPEX")
            if ds1 != ds2:
                raise DataError("Archived ISIN update dates differ")
            doc = {"as_of": ds1.isoformat(), "source_urls": [
                "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2",
                "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"],
                "securities": [{**s.__dict__, "listed_on": s.listed_on.isoformat()} for s in s1 + s2]}
            write_json(args.output, doc)
            print(Path(args.output).resolve())
            return 0
        cache = Cache(cfg["paths"]["cache"])
        if args.command == "verify-kd":
            if args.date > today:
                raise DataError("Future dates are prohibited")
            verify(args, cache, cfg)
            return 0
        if args.command == "sync":
            if args.end > today:
                raise DataError("Future dates are prohibited")
            cache.sync(source, args.start, args.end, refresh=args.refresh,
                       progress=lambda d: logging.info("sync date=%s", d))
            return 0
        report = run_screen(ScreenRequest(args.date, args.universe, args.offline, args.refresh),
                            cfg, cache, source, today)
        with SQLiteResultStore(cfg["paths"]["results"]) as store:
            version = store.add(report)
        report["result_version"] = version
        report["publication_status"] = "pending"
        as_of = report["as_of"]
        paths = export_report(report, cfg["paths"]["output"])
        print(json.dumps({"as_of": str(as_of), "counts": report["counts"],
                          "errors": len(report["errors"]), "version": version, "publication_status": "pending", "files": [str(p) for p in paths]}, ensure_ascii=False))
        return 0
    except (DataError, OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error) as exc:
        logging.error("run_failed: %s", exc)
        if cfg:
            write_json(Path(cfg["paths"]["output"]) / f"failure-{time.time_ns()}.json",
                       {"status": "failed", "command": args.command, "error": str(exc),
                        "generated_at": datetime.now(timezone.utc).isoformat()})
        else:
            print(str(exc), file=sys.stderr)
        return 2
    finally:
        if cache:
            cache.close()
        for handler in list(logging.getLogger().handlers):
            if isinstance(handler, logging.FileHandler):
                handler.close()
                logging.getLogger().removeHandler(handler)
