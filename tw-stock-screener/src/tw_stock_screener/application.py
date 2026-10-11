"""Selection use case shared by CLI and future scheduled/API adapters.

No printing, argument parsing, publishing or result approval belongs here.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import logging

from . import __version__
from .cache import resolve_date
from .demand import DemandHistory
from .models import DataError
from .output import write_json
from .screener import screen
from .universe import load_universe


def fingerprint(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ScreenRequest:
    date: date | None = None
    universe: str | None = None
    offline: bool = False
    refresh: bool = False
    evaluation_limit: int | None = None


def run_screen(request, cfg, cache, source, today, history_factory=DemandHistory):
    """Fetch only needed inputs; preserve existing selection semantics."""
    if request.date and request.date > today:
        raise DataError("Future dates are prohibited")
    universe_path = request.universe
    if request.date and not universe_path:
        raise DataError("--date requires --universe with an official snapshot dated that day")
    if request.date:
        securities, metadata = load_universe(universe_path, request.date)
    as_of = resolve_date(cache, source, request.date or today,
                         cfg["network"]["latest_search_days"], request.offline,
                         exact=request.date is not None)
    if request.date is None:
        if universe_path is None:
            if request.offline:
                raise DataError("Offline latest run requires --universe")
            universe_path = Path(cfg["paths"]["cache"]).parent / f"universe-{today}.json"
            if request.refresh or not universe_path.exists():
                write_json(universe_path, source.universe())
        securities, metadata = load_universe(universe_path, as_of, latest=True)
        if date.fromisoformat(metadata["as_of"]) != today:
            raise DataError("Latest mode requires today's official ISIN snapshot; use --date for historical runs")
    if request.refresh:
        for market in ["TWSE", "TPEX"]:
            cache.ensure(source, market, as_of, request.offline, refresh=True)
    provider = history_factory(cache, source, as_of, securities, cfg,
                             request.offline, request.refresh)
    calendar = provider.calendar(as_of - timedelta(days=45), as_of)
    quotes = cache.quotes(as_of, as_of)
    logging.getLogger(__name__).info("On-demand screen date=%s; results require explicit approval", as_of)
    inputs = {"universe": fingerprint(json.loads(Path(universe_path).read_text(encoding="utf-8"))),
              "target_quotes": fingerprint([q.record() for q in quotes]),
              "calendar": fingerprint([str(d) for d in calendar]), "stages": [], "previous_quotes": []}

    def stage_loader(market, code, frame):
        history, expected = provider.stage(market, code, frame)
        inputs["stages"].append({"market": market, "code": code, "frame": frame,
            "sha256": fingerprint({"bars": [q.record() for q in history],
                                   "calendar": [str(d) for d in expected]})})
        return history, expected

    def volume_loader(market, code, previous):
        quote = provider.previous_quote(market, code, previous)
        inputs["previous_quotes"].append({"market": market, "code": code,
                                         "sha256": fingerprint(quote.record())})
        return quote

    report = screen(as_of, quotes, securities, calendar, cfg,
                    stage_loader=stage_loader, volume_loader=volume_loader,
                    evaluation_limit=request.evaluation_limit)
    report.update({"status": "complete", "universe": metadata, "config": cfg,
        "history_mode": "on_demand_by_stage", "volume_unit": "shares",
        "history_months": {"daily": cfg["history"]["daily_months"],
                           "weekly": cfg["history"]["weekly_months"],
                           "monthly": cfg["history"]["months"]},
        "price_basis": "unadjusted", "change_basis": "official_reference",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance": {"schema_version": 1, "engine_version": __version__,
                       "engine_sha256": fingerprint({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                                      for p in sorted(Path(__file__).parent.glob("*.py"))}),
                       "rules_sha256": fingerprint({k: cfg[k] for k in ["selection", "kd", "history"]}),
                       "inputs": inputs}})
    return report
