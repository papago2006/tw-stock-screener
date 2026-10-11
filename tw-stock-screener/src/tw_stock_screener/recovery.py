"""Official archived-response reuse and fixed-cutoff recorded-run recovery."""
from pathlib import Path
import gzip
import hashlib
import json
import logging
from urllib.parse import urlencode
from .cache import Cache
from .models import DataError
from .sources import OfficialSource, raw_response_path


class ArchiveFirstSource(OfficialSource):
    """Reuse hash-verified official responses; fetch only missing responses."""
    def __init__(self, network, raw_dir):
        super().__init__(network, raw_dir)
        self.index = {}
        self.archive_hits = self.network_calls = 0
        manifest = self.raw_dir / "requests.jsonl"
        if manifest.exists():
            with manifest.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        record = json.loads(line)
                        self.index[record["url"]] = record["sha256"]
                    except (ValueError, KeyError, TypeError):
                        logging.warning("Skipping invalid archive manifest line")

    def fetch(self, url):
        digest = self.index.get(url)
        if digest:
            path = raw_response_path(self.raw_dir, digest)
            if path.is_file():
                try:
                    content = gzip.decompress(path.read_bytes())
                    if hashlib.sha256(content).hexdigest() != digest:
                        raise ValueError("Archive content SHA-256 mismatch")
                    doc = json.loads(content)
                    stat = str(doc.get("stat", "")).strip()
                    if stat.lower() == "ok" or stat in {
                            "很抱歉，沒有符合條件的資料!", "很抱歉，沒有符合條件的資料！", "查無資料"}:
                        self.archive_hits += 1
                        logging.info("reuse_raw url=%s sha256=%s", url, digest)
                        return content
                except (ValueError, OSError, AttributeError) as exc:
                    logging.warning("unusable_raw url=%s error=%s", url, exc)
        self.network_calls += 1
        logging.info("network_needed count=%s url=%s", self.network_calls, url)
        return super().fetch(url)


class RecoveryCache(Cache):
    """Preserve recorded closed-day statuses after the original price cutoff."""
    def __init__(self, path, cutoff):
        super().__init__(path)
        self.cutoff = cutoff

    def ensure(self, source, market, day, offline=False, refresh=False):
        if day > self.cutoff:
            if self.status(market, day) == "closed" and not refresh:
                return
            raise DataError("Recovery cannot fetch or infer sessions after the original cutoff")
        return super().ensure(source, market, day, offline, refresh)

