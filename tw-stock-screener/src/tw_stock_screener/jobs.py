"""Managed-job locking, bounded failure memory and observational progress.

No selection logic or result approval belongs here. OS locks are automatically
released on process exit; lock files stay in place to avoid unlink races.
"""
from datetime import datetime, timezone
from pathlib import Path
import json
import logging
import os
import re
import time

from .models import DataError, NoData
from .output import write_json
from .recovery import ArchiveFirstSource


def now():
    return datetime.now(timezone.utc).isoformat()


class JobLock:
    def __init__(self, path):
        self.path, self.file = Path(path), None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0, 2)
        if not self.file.tell():
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise DataError("已有選股工作執行中，請使用 work-status 查看；不啟動第二份工作") from exc
        return self

    def __exit__(self, *args):
        if self.file:
            try:
                self.file.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            finally:
                self.file.close()
                self.file = None


class WorkflowSource(ArchiveFirstSource):
    """Reuse official raw; remember exhausted monthly failures for exact replay.

    Transport retries are owned by OfficialSource, never multiplied here.
    Failed months are not saved as complete market data. A new job gets its own
    failure budget; resuming this job preserves its exhausted failures.
    """
    def __init__(self, network, raw_dir, failure_path):
        super().__init__(network, raw_dir)
        self.failure_path = Path(failure_path)
        self.failures = (json.loads(self.failure_path.read_text(encoding="utf-8"))
                         if self.failure_path.exists() else {})
        self.replay_only = False

    @staticmethod
    def key(market, code, month):
        return f"{market}/{code}/{month}"

    def history_failure(self, market, code, month):
        record = self.failures.get(self.key(market, code, month))
        return record["reason"] if record else None

    def daily_failure(self, market, day):
        record = self.failures.get(f"daily/{market}/{day}")
        return record["reason"] if record else None

    def daily(self, market, day):
        previous = self.daily_failure(market, day)
        if previous:
            raise DataError(previous)
        try:
            return super().daily(market, day)
        except NoData:
            raise
        except DataError as exc:
            if "Official API failed" not in str(exc):
                raise RuntimeError(f"官方日報格式或資料異常，停止工作：{exc}") from exc
            self.failures[f"daily/{market}/{day}"] = {"reason": str(exc), "recorded_at": now(),
                                                     "category": "api_unavailable"}
            write_json(self.failure_path, self.failures)
            raise

    def fetch(self, url):
        if self.replay_only:
            raise RuntimeError("離線驗證禁止任何HTTP或原始回應回填")
        return super().fetch(url)

    def monthly(self, market, code, month, as_of):
        previous = self.history_failure(market, code, month)
        if previous:
            raise DataError(previous)
        try:
            return super().monthly(market, code, month, as_of)
        except DataError as exc:
            reason = str(exc)
            # Structural ambiguity must stop the managed job, not silently
            # turn schema changes into per-stock non-selection.
            structural = ("schema", "Malformed", "Wrong historical", "Duplicate/wrong",
                          "Unexpected TPEx", "Unknown/ambiguous", "invalid JSON",
                          "Unverified TPEx", "Invalid raw response", "Invalid date", "disk reserve")
            if isinstance(exc.__cause__, json.JSONDecodeError) or any(token.lower() in reason.lower() for token in structural):
                raise RuntimeError(f"官方資料結構或必要存檔異常，停止工作：{reason}") from exc
            self.failures[self.key(market, code, month)] = {"reason": reason, "recorded_at": now(),
                "category": "api_unavailable" if "Official API failed" in reason else "unavailable_history"}
            write_json(self.failure_path, self.failures)
            raise


class JobProgress(logging.Handler):
    def __init__(self, directory, source, state):
        super().__init__()
        self.directory, self.source, self.state = Path(directory), source, state
        self.started = time.monotonic()

    def save(self):
        self.state.update(updated_at=now(), network_calls=self.source.network_calls,
                          archive_hits=self.source.archive_hits,
                          elapsed_seconds=round(time.monotonic()-self.started, 2))
        try:
            write_json(self.directory / "run-state.json", self.state)
        except OSError as exc:
            self.state["progress_write_errors"] = self.state.get("progress_write_errors", 0)+1
            logging.warning("progress_write_failed; calculation continues: %s", exc)

    def phase(self, phase):
        self.state.update(status="running", phase=phase, candidate=None, processed=0)
        self.save()

    def emit(self, record):
        message = record.getMessage()
        if message.startswith("candidate rank="):
            match = re.search(r"rank=(\d+)/(\d+)", message)
            if match:
                self.state.update(candidate=message, current_rank=int(match[1]), total=int(match[2]))
        elif message.startswith("candidate_result"):
            self.state["processed"] = self.state.get("current_rank", 0)
        if message.startswith(("candidate rank=", "candidate_result", "stage_history", "network_needed", "request_failed")):
            self.state["last_event"] = message
            self.save()


def read_status(pointer):
    pointer = Path(pointer)
    if not pointer.exists():
        return {"status": "not_started", "message": "尚未建立工作"}
    reference = json.loads(pointer.read_text(encoding="utf-8"))
    directory = Path(reference["job_directory"])
    state_path = directory / "run-state.json"
    if not state_path.exists():
        return {**reference, "status": "starting", "message": "已建立工作，等待進度"}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    summary_path = directory / "summary.json"
    if summary_path.exists():
        return {**reference, **state, **json.loads(summary_path.read_text(encoding="utf-8"))}
    if state.get("status") in {"starting", "running"}:
        try:
            with JobLock(state["lock_path"]):
                state.update(status="interrupted", message="工作已中斷；重新執行相同命令可沿用月份快取續跑")
        except DataError:
            pass
    return {**reference, **state}
