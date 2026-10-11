"""Finite local workflow: sample gate -> full selection -> replay -> pending version.

Composes application/services, never duplicates KD or selection formulas.
Monthly cache is the durable resume point; completed calculations and exhausted
request failures are persisted separately. Nothing here approves a result.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
import hashlib
import json
import logging
import os
import time

from .application import ScreenRequest, run_screen, fingerprint
from .cache import Cache
from .demand import CheckedDemandHistory
from .diagnostics import RawEvidence, diagnose_report
from .jobs import JobLock, JobProgress, WorkflowSource, now
from .models import DataError
from .output import write_json, export_report
from .presentation import write_summary, outcome_counts
from .recovery import RecoveryCache
from .results import SQLiteResultStore
from .universe import load_universe


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def prepare_recorded(cfg, directory):
    """Verify immutable inputs of an already-created recovery cache."""
    directory = Path(directory).resolve()
    saved = read_json(directory / "recovery.json")
    original_path = Path(saved["original_report"])
    universe_path = directory / "universe.json"
    for path, key in [(original_path, "original_sha256"), (universe_path, "universe_sha256")]:
        if hashlib.sha256(path.read_bytes()).hexdigest() != saved[key]:
            raise DataError(f"原始輸入已變動，停止恢復：{path}")
    original = read_json(original_path)
    for section in ("selection", "kd", "history"):
        if cfg[section] != original["config"][section]:
            raise DataError(f"原執行與目前{section}設定不一致，不能冒充相同條件的續跑")
    target = date.fromisoformat(saved["target_day"])
    reference = date.fromisoformat(saved["reference_day"])
    _, metadata = load_universe(universe_path, target, latest=True)
    if metadata != original["universe"]:
        raise DataError("原分類快照中繼資料不一致")
    updated = deepcopy(cfg)
    updated["paths"].update(cache=str(directory / "market.sqlite3"), raw=original["config"]["paths"]["raw"])
    if not Path(updated["paths"]["cache"]).is_file():
        raise DataError("恢復行情副本不存在；不重建或覆寫原資料")
    return updated, ScreenRequest(universe=str(universe_path)), reference, target, original, saved


def compare_replay(report, replay):
    fields = ("as_of", "previous_session", "counts", "results", "candidates", "errors")
    checks = {key: report[key] == replay[key] for key in fields}
    checks["input_hashes"] = report["provenance"]["inputs"] == replay["provenance"]["inputs"]
    return {"matched": all(checks.values()), "checks": checks,
            "external_kd_software_verified": False}


def check_original_ranking(report, original):
    if original is None:
        return
    rows = report.get("validation", {}).get("ranking", report["candidates"])
    keys = lambda items: [(r["market"], r["code"], r["rank"]) for r in items]
    if report["as_of"] != original["as_of"] or keys(rows) != keys(original["candidates"]):
        raise DataError("原目標交易日或前300排名改變，停止保存待確認結果")


def run_workflow(cfg, request, today, recorded_dir=None, new_run=False):
    cfg = deepcopy(cfg)
    target, original, recovery = None, None, None
    if recorded_dir:
        if request.date or request.universe or request.refresh:
            raise DataError("recorded-run不可混用date、universe或refresh")
        offline = request.offline
        cfg, request, today, target, original, recovery = prepare_recorded(cfg, recorded_dir)
        request = replace(request, offline=offline)
    root_output = Path(cfg["paths"]["output"])
    engine = fingerprint({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(Path(__file__).parent.glob("*.py"))})
    identity = {"request_date": str(request.date) if request.date else None, "reference_day": str(today),
                "universe_path": str(request.universe), "refresh": request.refresh,
                "configuration": cfg, "rules": {k: cfg[k] for k in ("selection", "kd", "history")},
                "engine_sha256": engine, "recovery": recovery,
                "supplied_universe_sha256": hashlib.sha256(Path(request.universe).read_bytes()).hexdigest() if request.universe else None}
    if new_run:
        identity["new_run"] = now()
    job_id = fingerprint(identity)[:20]
    directory = root_output / "jobs" / job_id
    lock_path = Path(cfg["paths"]["cache"]).parent / "screener-work.lock"
    pointer = root_output / "work-current.json"
    # Acquire the shared-cache lock before mutating pointers or creating state.
    with JobLock(lock_path):
        directory.mkdir(parents=True, exist_ok=True)
        write_json(pointer, {"job_id": job_id, "job_directory": str(directory), "lock_path": str(lock_path)})
        if (directory / "summary.json").exists():
            return read_json(directory / "summary.json")
        identity_path = directory / "identity.json"
        if identity_path.exists() and read_json(identity_path) != identity:
            raise DataError("工作識別不一致，拒絕覆寫")
        write_json(identity_path, identity)
        cfg["paths"]["output"] = str(directory / "output")
        write_json(directory / "effective-config.json", cfg)
        state = {"status": "starting", "job_id": job_id, "pid": os.getpid(), "started_at": now(),
                 "job_directory": str(directory), "lock_path": str(lock_path),
                 "target_day": str(target) if target else str(request.date or today), "trial_version": True}
        source = WorkflowSource(cfg["network"], cfg["paths"]["raw"], directory / "request-failures.json")
        progress = JobProgress(directory, source, state)
        log = logging.FileHandler(directory / "run.log", encoding="utf-8")
        log.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger = logging.getLogger()
        logger.addHandler(log)
        logger.addHandler(progress)
        cache = None
        progress.save()
        try:
            cache = RecoveryCache(cfg["paths"]["cache"], target) if target else Cache(cfg["paths"]["cache"])
            sample_size = min(30, cfg["selection"]["top_n"])

            def calculate(req):
                calculated = run_screen(req, cfg, cache, source, today, history_factory=CheckedDemandHistory)
                if calculated["provenance"]["engine_sha256"] != engine:
                    raise RuntimeError("工作執行期間程式檔案變動，停止保存；重新啟動可沿用行情快取")
                return calculated

            def verify(report, req):
                previous_calls = source.network_calls
                source.replay_only = True
                try:
                    replay = calculate(replace(req, offline=True, refresh=False))
                finally:
                    source.replay_only = False
                verification = compare_replay(report, replay)
                verification["checks"]["no_http_during_replay"] = source.network_calls == previous_calls
                verification["matched"] = all(verification["checks"].values())
                if not verification["matched"]:
                    write_json(directory / f"mismatch-{time.time_ns()}.json", verification)
                    raise DataError("離線重算不一致，停止擴大及保存待確認結果")
                return verification

            progress.phase("sample_validation")
            sample_request = replace(request, evaluation_limit=sample_size)
            sample = calculate(sample_request)
            state["target_day"] = sample["as_of"]
            check_original_ranking(sample, original)
            verification = verify(sample, sample_request)
            # Fix latest mode to the snapshot captured for this finite job.
            snapshot = request.universe or str(Path(cfg["paths"]["cache"]).parent / f"universe-{today}.json")
            request = replace(request, universe=snapshot, refresh=False)
            securities, _ = load_universe(snapshot, date.fromisoformat(sample["as_of"]), latest=request.date is None)
            evidence = RawEvidence(cfg["paths"]["raw"])
            progress.phase("sample_diagnosis")
            diagnosis = diagnose_report(sample, cache, securities, cfg, evidence)
            write_json(directory / "sample.json", sample)
            write_json(directory / "sample-verification.json", verification)
            write_json(directory / "sample-diagnostics.json", diagnosis)
            write_summary(directory / "sample-summary.md", sample, verification, diagnosis)

            progress.phase("full_selection")
            full_path = directory / "calculation.json"
            report = read_json(full_path) if full_path.exists() else calculate(request)
            check_original_ranking(report, original)
            if len(report["candidates"]) != report["counts"]["top_n"]:
                raise DataError("完整候選數量與排名不一致")
            if report.get("report_scope", "full") != "full":
                raise DataError("樣本不可當作完整結果")
            report["job"] = {"id": job_id, "trial_version": True}
            if recovery:
                report["recovery"] = recovery
            write_json(full_path, report)
            progress.phase("full_offline_verification")
            verification = verify(report, request)
            write_json(directory / "verification.json", verification)
            progress.phase("full_diagnosis")
            diagnosis = diagnose_report(report, cache, securities, cfg, evidence)
            write_json(directory / "diagnostics.json", diagnosis)
            report.update(verification=verification, publication_status="pending", trial_version=True)
            progress.phase("saving_pending_result")
            with SQLiteResultStore(cfg["paths"]["results"]) as store:
                # Recover a committed version if interruption occurred between
                # SQLite commit and artifact export. Never approve anything.
                version = next((v["version"] for v in store.versions(report["as_of"])
                    if store.get(v["version"])["report"].get("job", {}).get("id") == job_id), None)
                if version is None:
                    version = store.add(report)
            report["result_version"] = version
            paths = export_report(report, cfg["paths"]["output"])
            summary_path = write_summary(directory / "report.md", report, verification, diagnosis, version)
            counts = outcome_counts(report)
            summary = {"status": "complete_with_exclusions" if report["errors"] else "complete",
                       "job_id": job_id, "as_of": report["as_of"], "publication_status": "pending",
                       "version": version, "outcomes": counts, "counts": report["counts"],
                       "verification": verification, "external_kd_software_verified": False,
                       "files": [str(p) for p in paths]+[str(summary_path), str(directory / "diagnostics.json")],
                       "finished_at": now(), "network_calls": source.network_calls, "archive_hits": source.archive_hits,
                       "job_directory": str(directory)}
            write_json(directory / "summary.json", summary)
            state.update(summary, phase="finished", processed=len(report["candidates"]))
            progress.save()
            return summary
        except (Exception, KeyboardInterrupt) as exc:
            state.update(status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                         error=str(exc), finished_at=now())
            logging.exception("managed_work_failed")
            progress.save()
            write_json(directory / f"failure-{time.time_ns()}.json", state)
            raise
        finally:
            if cache:
                cache.close()
            logger.removeHandler(progress)
            logger.removeHandler(log)
            log.close()
