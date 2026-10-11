from datetime import datetime, timezone
from pathlib import Path
import csv
import json
import os
import tempfile
import time

FIELDS = ["rank", "market", "code", "name", "change_pct", "volume", "volume_ratio",
          "daily_k", "daily_d", "weekly_k", "weekly_d", "monthly_k", "monthly_d"]


def write_json(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Same-filesystem atomic replacement: no half-written public artifact.
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False, suffix=".tmp") as f:
            temp = Path(f.name)
            json.dump(doc, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
        for attempt in range(4):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                # Windows readers/scanners may briefly hold the destination.
                # Bounded retry; persistent critical-write failures still raise.
                if attempt == 3:
                    raise
                time.sleep(.05 * 2 ** attempt)
    finally:
        if temp is not None and temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def export_report(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    prefix = (f"validation-{report['validation']['limit']}" if
              report.get("report_scope") == "validation_sample" else "screen")
    base = directory / f"{prefix}-{report['as_of']}-{stamp}"
    write_json(base.with_suffix(".json"), report)
    with base.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["results"])
    return base.with_suffix(".json"), base.with_suffix(".csv")
