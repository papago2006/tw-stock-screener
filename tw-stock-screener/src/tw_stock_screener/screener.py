from .indicators import aggregate, last_kd
from .models import DataError
import logging

LOG = logging.getLogger(__name__)


def validate_history(history, expected, as_of, strict_volume=True):
    if len({b.date for b in history}) != len(history):
        raise DataError("Duplicate history dates")
    expected = set(expected)
    if not history or not expected or as_of not in expected or history[0].date > min(expected):
        raise DataError("History does not reach configured warmup start/target")
    observed = {b.date for b in history}
    if not expected.issubset(observed):
        raise DataError(f"History missing {len(expected - observed)} market sessions")
    if any(not (b.valid_bar() if strict_volume else b.valid_prices()) for b in history):
        raise DataError("Invalid/missing unadjusted OHLCV" if strict_volume else "Invalid/missing unadjusted OHLC")


def screen(as_of, quotes, universe, calendar, config, history_loader=None,
           stage_loader=None, volume_loader=None, evaluation_limit=None):
    """Pure selection core: input may contain future rows; these are discarded first."""
    if evaluation_limit is not None and (type(evaluation_limit) is not int
            or not 0 < evaluation_limit <= config["selection"]["top_n"]):
        raise DataError("Validation limit must be a positive integer within top_n")
    calendar = sorted(set(d for d in calendar if d <= as_of))
    if as_of not in calendar or len(calendar) < 2:
        raise DataError("Missing target or previous market trading day")
    previous = calendar[-2]
    eligible = {(s.market, s.code): s for s in universe if s.ordinary and s.listed_on <= as_of}
    histories = {}
    for q in quotes:
        if q.date <= as_of and (q.market, q.code) in eligible:
            histories.setdefault((q.market, q.code), []).append(q)
    counts = dict.fromkeys(["universe", "quoted", "rankable", "top_n", "daily_kd", "weekly_kd",
                           "monthly_kd", "volume_ratio"], 0)
    counts["universe"] = len(eligible)
    errors, ranked = [], []

    def error(key, stage, reason):
        errors.append({"market": key[0], "code": key[1], "stage": stage, "reason": str(reason)})

    for key, sec in eligible.items():
        history = sorted(histories.get(key, []), key=lambda q: q.date)
        today = [q for q in history if q.date == as_of]
        if len(today) != 1:
            error(key, "ranking", "Missing/duplicate target quote")
            continue
        q = today[0]
        counts["quoted"] += 1
        pct = q.change_pct
        if pct is None:
            error(key, "ranking", "Unknown official percentage-change reference")
            continue
        ranked.append((pct, key, q, history))
    ranked.sort(key=lambda item: (-item[0], item[1][1], item[1][0]))
    counts["rankable"] = len(ranked)
    candidates = ranked[:config["selection"]["top_n"]]
    counts["top_n"] = len(candidates)
    results, audit = [], []
    evaluated = candidates if evaluation_limit is None else candidates[:evaluation_limit]
    for rank, (pct, key, q, history) in enumerate(evaluated, 1):
        record = {"rank": rank, "market": q.market, "code": q.code, "name": q.name,
                  "change_pct": pct, "volume": q.volume, "volume_ratio": None}
        audit.append(record)
        stage = "history"
        try:
            LOG.info("candidate rank=%s/%s market=%s code=%s", rank, len(evaluated), *key)
            if history_loader is not None:
                history = sorted((b for b in history_loader(*key) if b.date <= as_of), key=lambda b: b.date)
            if stage_loader is None:
                validate_history(history, {d for d in calendar if d >= eligible[key].listed_on}, as_of)
            passed = True
            for frame in ["daily", "weekly", "monthly"]:
                stage = frame + "_kd"
                if stage_loader is not None:
                    loaded, expected = stage_loader(*key, frame)
                    history = sorted((b for b in loaded if b.date <= as_of), key=lambda b: b.date)
                    if any((b.market, b.code) != key for b in history):
                        raise DataError("Stage history contains a different security")
                    validate_history(history, [d for d in expected if d <= as_of], as_of, strict_volume=False)
                    today_bar = next((b for b in history if b.date == as_of), None)
                    if today_bar is None or any(getattr(today_bar, p) != getattr(q, p)
                                               for p in ["open", "high", "low", "close"]):
                        raise DataError("Historical target OHLC differs from official daily snapshot")
                    record[frame + "_history_start"] = history[0].date.isoformat()
                k, d = last_kd(aggregate(history, frame, as_of), config["kd"])
                record[frame + "_k"] = k
                record[frame + "_d"] = d
                if k <= d:
                    record["rejected_at"] = stage
                    passed = False
                    break
                counts[stage] += 1
            if not passed:
                LOG.info("candidate_result market=%s code=%s rejected_at=%s", *key, record["rejected_at"])
                continue
            stage = "volume_ratio"
            prior = (volume_loader(*key, previous) if volume_loader is not None else
                     next((b for b in history if b.date == previous), None))
            if q.volume is None or q.volume < 0:
                raise DataError("Target session exact volume missing/invalid")
            if prior is None or prior.volume is None or prior.volume <= 0:
                raise DataError("Previous market-session volume missing or zero")
            ratio = q.volume / prior.volume
            record["volume_ratio"] = ratio
            if ratio >= config["selection"]["volume_ratio"]:
                counts[stage] += 1
                results.append(record.copy())
                record["rejected_at"] = None
            else:
                record["rejected_at"] = stage
        except DataError as exc:
            record["rejected_at"] = stage
            error(key, stage, exc)
        LOG.info("candidate_result market=%s code=%s rejected_at=%s", *key, record.get("rejected_at"))
    report = {"as_of": as_of.isoformat(), "previous_session": previous.isoformat(),
            "counts": counts, "results": results, "candidates": audit, "errors": errors}
    if evaluation_limit is not None:
        report.update(report_scope="validation_sample", validation={
            "limit": evaluation_limit, "evaluated": len(evaluated),
            "unevaluated": len(candidates) - len(evaluated),
            "ranking": [{"rank": rank, "market": key[0], "code": key[1], "change_pct": pct}
                        for rank, (pct, key, _, _) in enumerate(candidates, 1)]})
    return report
