"""Resume an existing latest-mode run, preserving its dated universe warning.

This is recovery of a recorded run, not the strict historical-date CLI. The
normal screen --date contract is unchanged. Original data/code are read only;
a SQLite backup is used for incremental updates. No result is auto-approved.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
import argparse
import gzip
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
from urllib.parse import parse_qs, urlparse

from tw_stock_screener.application import ScreenRequest, run_screen
from tw_stock_screener.recovery import ArchiveFirstSource, RecoveryCache
from tw_stock_screener.cache import Cache, months_before
from tw_stock_screener.config import load_config
from tw_stock_screener.demand import months, end_of_month
from tw_stock_screener.models import DataError
from tw_stock_screener.output import write_json, export_report
from tw_stock_screener.results import SQLiteResultStore
from tw_stock_screener.sources import OfficialSource, raw_response_path
from tw_stock_screener.universe import load_universe






def inventory(cache, source, original, securities, cfg):
    """Conditional upper bounds, never pretend failed stocks will pass KD."""
    as_of = date.fromisoformat(original["as_of"])
    listed = {(s.market, s.code): s.listed_on for s in securities}
    cached = {(m, c, month): through for m, c, month, through in cache.db.execute(
        "SELECT market,code,month,through FROM stock_months")}
    raw_keys = set()
    existing_digests = {p.stem for p in source.raw_dir.glob("*.gz")}
    existing_digests.update(p.stem for p in (source.raw_dir / "v2").glob("*/*.gz"))
    for url, digest in source.index.items():
        parsed = urlparse(url)
        q = parse_qs(parsed.query)
        market = "TWSE" if parsed.path.endswith("/STOCK_DAY") else (
            "TPEX" if parsed.path.endswith("/tradingStock") else None)
        if market:
            code = q.get("stockNo" if market == "TWSE" else "code", [None])[0]
            stamp = q.get("date", [""])[0].replace("/", "")
            if code and len(stamp) == 8 and digest in existing_digests:
                raw_keys.add((market, code, f"{stamp[:4]}-{stamp[4:6]}-01"))
    result = {"note": "Upper bounds if all original top-300 reach each stage; actual fetches stop on rejection.",
              "original_candidates": len(original["candidates"]), "stages": {}}
    for frame, window in [("daily", "daily_months"), ("weekly", "weekly_months"), ("monthly", "months")]:
        counts = dict(sqlite_months=0, raw_months_available=0, potentially_missing_network_months=0)
        for row in original["candidates"]:
            key = row["market"], row["code"]
            start = max(months_before(as_of, cfg["history"][window]), listed[key])
            for month in months(start, as_of):
                month_key = (*key, str(month))
                through = str(min(end_of_month(month), as_of))
                if cached.get(month_key, "") >= through:
                    counts["sqlite_months"] += 1
                elif month_key in raw_keys:
                    counts["raw_months_available"] += 1
                else:
                    counts["potentially_missing_network_months"] += 1
        result["stages"][frame] = counts
    return result


class Progress(logging.Handler):
    def __init__(self, path, source, state):
        super().__init__()
        self.path, self.source, self.state = path, source, state

    def emit(self, record):
        message = record.getMessage()
        if message.startswith("candidate rank="):
            self.state["candidate"] = message
        if message.startswith(("candidate rank=", "candidate_result", "network_needed", "stage_history")):
            self.state.update(status="running", last_event=message,
                              updated_at=datetime.now(timezone.utc).isoformat(),
                              archive_hits=self.source.archive_hits,
                              network_calls=self.source.network_calls)
            self.save()

    def save(self):
        try:
            write_json(self.path, self.state)
        except OSError as exc:
            # Observability must not abort selection. Critical market/result
            # writes still propagate errors normally; the append-only log stays.
            self.state["progress_write_errors"] = self.state.get("progress_write_errors", 0) + 1
            self.state["last_progress_error"] = str(exc)
            logging.warning("progress_write_failed; continuing calculation: %s", exc)


def recover(args):
    original_path = Path(args.original_report).resolve()
    universe_path = Path(args.universe).resolve()
    run_dir = Path(args.run_dir).resolve()
    limit = getattr(args, "limit", None)
    offline = getattr(args, "offline", False)
    original = json.loads(original_path.read_text(encoding="utf-8"))
    cfg = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
    for section in ("selection", "kd", "history"):
        if cfg[section] != original["config"][section]:
            raise DataError(f"Recovery refuses changed {section} settings")
    if limit is not None and (type(limit) is not int or not 0 < limit <= cfg["selection"]["top_n"]):
        raise DataError("Invalid validation sample size")
    as_of = date.fromisoformat(original["as_of"])
    reference_day = date.fromisoformat(original["universe"]["as_of"])
    securities, metadata = load_universe(universe_path, as_of, latest=True)
    if metadata != original["universe"]:
        raise DataError("Recovery universe metadata differs from original run")
    original_db = Path(original["config"]["paths"]["cache"]).resolve()
    recovery = {"original_report": str(original_path),
                "original_sha256": hashlib.sha256(original_path.read_bytes()).hexdigest(),
                "universe_sha256": hashlib.sha256(universe_path.read_bytes()).hexdigest(),
                "original_cache": str(original_db), "reference_day": str(reference_day),
                "target_day": str(as_of), "mode": "recovery_of_recorded_latest_run",
                "warning": metadata["warning"]}
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = run_dir / "recovery.json"
    copied_db = run_dir / "market.sqlite3"
    if manifest.exists():
        if json.loads(manifest.read_text(encoding="utf-8")) != recovery or not copied_db.is_file():
            raise DataError("Recovery directory belongs to different inputs or is incomplete")
    else:
        if copied_db.exists():
            raise DataError("Refusing to overwrite unlabelled recovery database")
        if shutil.disk_usage(run_dir).free < original_db.stat().st_size * 2 + 1024 ** 3:
            raise DataError("Insufficient space for safe recovery backup")
        with sqlite3.connect(original_db.as_uri() + "?mode=ro", uri=True) as old:
            with sqlite3.connect(copied_db) as new:
                old.backup(new)
        shutil.copy2(universe_path, run_dir / "universe.json")
        write_json(manifest, recovery)
    if hashlib.sha256((run_dir / "universe.json").read_bytes()).hexdigest() != recovery["universe_sha256"]:
        raise DataError("Saved recovery universe content changed")
    cfg = deepcopy(cfg)
    job_dir = run_dir / f"validation-{limit}" if limit is not None else run_dir
    job_dir.mkdir(parents=True, exist_ok=True)
    cfg["paths"].update(cache=str(copied_db), raw=original["config"]["paths"]["raw"],
                        output=str(job_dir / "output"))
    write_json(job_dir / "effective-config.json", cfg)
    state = {"status": "starting", "pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat(),
             "target_day": str(as_of), "run_directory": str(job_dir), "evaluation_limit": limit,
             "report_scope": "validation_sample" if limit is not None else "full"}
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(job_dir / "run.log", encoding="utf-8"),
                                  logging.StreamHandler()], force=True)
    source = ArchiveFirstSource(cfg["network"], cfg["paths"]["raw"])
    progress = Progress(job_dir / "run-state.json", source, state)
    logging.getLogger().addHandler(progress)
    progress.save()
    cache = RecoveryCache(copied_db, as_of)
    started = time.monotonic()
    try:
        considered = original if limit is None else {**original, "candidates": original["candidates"][:limit]}
        write_json(job_dir / "inventory.json", inventory(cache, source, considered, securities, cfg))
        logging.info("recovery_started pid=%s target=%s reference_day=%s", os.getpid(), as_of, reference_day)
        request = ScreenRequest(universe=str(run_dir / "universe.json"),
                                offline=offline, evaluation_limit=limit)
        report = run_screen(request,
                            cfg, cache, source, reference_day)
        if report["as_of"] != str(as_of):
            raise DataError("Resolved session changed; refusing a different result")
        keys = lambda rows: [(r["market"], r["code"], r["rank"]) for r in rows]
        ranking = report["validation"]["ranking"] if limit is not None else report["candidates"]
        if keys(ranking) != keys(original["candidates"]):
            raise DataError("Top-300 ranking changed; original recovery requires inspection")
        report["recovery"] = recovery
        version = None
        verification = None
        if limit is not None:
            if len(report["candidates"]) != min(limit, len(original["candidates"])):
                raise DataError("Wrong sample size")
            # Pure offline replay uses exactly the same core and saved inputs.
            state["phase"] = "offline_replay"
            replay = run_screen(ScreenRequest(universe=request.universe, offline=True,
                                              evaluation_limit=limit), cfg, cache, source, reference_day)
            fields = ["as_of", "previous_session", "counts", "results", "candidates", "errors", "validation"]
            matches = {key: report[key] == replay[key] for key in fields}
            matches["input_hashes"] = report["provenance"]["inputs"] == replay["provenance"]["inputs"]
            verification = {"offline_replay_matches": all(matches.values()), "checks": matches,
                            "external_kd_software_verified": False,
                            "sample_data_errors": [e for e in report["errors"] if e["stage"] != "ranking"],
                            "ranking_errors": sum(e["stage"] == "ranking" for e in report["errors"])}
            write_json(job_dir / "verification.json", verification)
            if not all(matches.values()):
                raise DataError("Offline replay differed; sample validation failed")
            report["verification"] = verification
            report["publication_status"] = "validation_only"
        else:
            with SQLiteResultStore(cfg["paths"]["results"]) as store:
                version = store.add(report)
            report.update(result_version=version, publication_status="pending")
        paths = export_report(report, cfg["paths"]["output"])
        summary = {"status": "complete", "publication_status": report["publication_status"], "version": version,
                   "as_of": report["as_of"], "counts": report["counts"], "errors": len(report["errors"]),
                   "files": [str(p) for p in paths], "archive_hits": source.archive_hits,
                   "network_calls": source.network_calls, "elapsed_seconds": time.monotonic() - started,
                   "evaluated": len(report["candidates"]), "validation": verification}
        write_json(job_dir / "summary.json", summary)
        state.update(summary, finished_at=datetime.now(timezone.utc).isoformat())
        progress.save()
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except Exception as exc:
        logging.exception("recovery_failed")
        state.update(status="failed", error=str(exc), finished_at=datetime.now(timezone.utc).isoformat())
        progress.save()
        write_json(job_dir / f"failure-{time.time_ns()}.json", state)
        return 2
    finally:
        cache.close()
        logging.getLogger().removeHandler(progress)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-report", required=True)
    parser.add_argument("--universe", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--limit", type=int, help="Validate only the first N of the ranked top-300; never save as a daily result")
    parser.add_argument("--offline", action="store_true")
    sys.exit(recover(parser.parse_args()))
