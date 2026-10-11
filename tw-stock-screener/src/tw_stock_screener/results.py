"""Append-only result versions and transactional official pointers.

Market cache and result history are deliberately separate databases. A future
storage adapter can implement ResultStore without changing selection/tracking.
"""
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Protocol
from uuid import uuid4

from .models import DataError


class ResultStore(Protocol):
    def add(self, report: dict) -> str: ...
    def get(self, version: str) -> dict: ...
    def official(self, day: str) -> dict | None: ...
    def approve(self, version: str, expected_current: str | None, allow_errors: bool = False) -> dict: ...
    def versions(self, day: str | None = None) -> list[dict]: ...


def validate_report(report):
    if report.get("report_scope", "full") != "full":
        raise DataError("Validation samples cannot become daily result versions")
    if report.get("status") != "complete":
        raise DataError("Only completed calculations can become result versions")
    target = date.fromisoformat(report["as_of"])
    if date.fromisoformat(report["previous_session"]) >= target:
        raise DataError("Previous session must precede the result date")
    seen = set()
    for row in report["results"]:
        key = (row["market"], row["code"])
        if key in seen or key[0] not in {"TWSE", "TPEX"}:
            raise DataError("Duplicate/invalid result security")
        seen.add(key)
    if not isinstance(report["errors"], list):
        raise DataError("Result errors must be a list")


class SQLiteResultStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS versions (
            id TEXT PRIMARY KEY, day TEXT NOT NULL, created_at TEXT NOT NULL,
            sha256 TEXT NOT NULL, report TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS versions_day ON versions(day);
          CREATE TABLE IF NOT EXISTS official (
            day TEXT PRIMARY KEY, version TEXT NOT NULL REFERENCES versions(id));
          CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY, day TEXT NOT NULL, version TEXT NOT NULL REFERENCES versions(id),
            previous_version TEXT, approved_at TEXT NOT NULL, acknowledged_errors INTEGER NOT NULL);
        """)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    def add(self, report):
        validate_report(report)
        payload = json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False)
        version = uuid4().hex
        with self.db:
            self.db.execute("INSERT INTO versions VALUES (?,?,?,?,?)", (version, report["as_of"],
                datetime.now(timezone.utc).isoformat(), hashlib.sha256(payload.encode("utf-8")).hexdigest(), payload))
        return version

    def get(self, version):
        row = self.db.execute("SELECT * FROM versions WHERE id=?", (version,)).fetchone()
        if row is None:
            raise DataError(f"Unknown result version: {version}")
        if hashlib.sha256(row["report"].encode("utf-8")).hexdigest() != row["sha256"]:
            raise DataError("Result version integrity check failed")
        current = self.db.execute("SELECT version FROM official WHERE day=?", (row["day"],)).fetchone()
        return {"version": row["id"], "as_of": row["day"], "created_at": row["created_at"],
                "sha256": row["sha256"], "official": bool(current and current[0] == version),
                "report": json.loads(row["report"])}

    def official(self, day):
        date.fromisoformat(day)
        row = self.db.execute("SELECT version FROM official WHERE day=?", (day,)).fetchone()
        return self.get(row[0]) if row else None

    def versions(self, day=None):
        if day is not None:
            date.fromisoformat(day)
        rows = self.db.execute("""SELECT v.id AS version,v.day AS as_of,v.created_at,v.sha256,
            CASE WHEN o.version=v.id THEN 1 ELSE 0 END AS official
            FROM versions v LEFT JOIN official o ON o.day=v.day
            WHERE (? IS NULL OR v.day=?) ORDER BY v.day DESC,v.created_at DESC,v.id""", (day, day))
        return [{**dict(r), "official": bool(r["official"])} for r in rows]

    def approve(self, version, expected_current, allow_errors=False):
        # One writer transaction: another approval cannot slip between check and update.
        try:
            self.db.execute("BEGIN IMMEDIATE")
            item = self.get(version)
            report = item["report"]
            validate_report(report)
            if report["errors"] and not allow_errors:
                raise DataError("Result contains errors; inspect them and explicitly use --allow-errors")
            old = self.db.execute("SELECT version FROM official WHERE day=?", (item["as_of"],)).fetchone()
            old_version = old[0] if old else None
            if old_version != expected_current:
                raise DataError("Official version changed; review current result before approving")
            if old_version != version:
                self.db.execute("INSERT OR REPLACE INTO official VALUES (?,?)", (item["as_of"], version))
                self.db.execute("""INSERT INTO approvals
                    (day,version,previous_version,approved_at,acknowledged_errors) VALUES (?,?,?,?,?)""",
                    (item["as_of"], version, old_version, datetime.now(timezone.utc).isoformat(),
                     int(bool(report["errors"]) and allow_errors)))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return self.get(version)
