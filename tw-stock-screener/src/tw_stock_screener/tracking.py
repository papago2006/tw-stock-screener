"""Read-only tracking derived from exact previous-session official versions.

Recomputed on demand so revising an older official result cannot leave stale
downstream tracking snapshots. No network, files, CLI or selection rules here.
"""
from .models import DataError
from .results import ResultStore

METRICS = ("daily_k", "daily_d", "weekly_k", "weekly_d", "monthly_k", "monthly_d", "volume_ratio", "change_pct", "volume")


def by_security(report):
    return {(r["market"], r["code"]): r for r in report["results"]}


def track_official(store: ResultStore, day: str):
    current = store.official(day)
    if current is None:
        raise DataError(f"No official result for {day}")
    report = current["report"]
    previous = store.official(report["previous_session"])
    base = {"as_of": day, "version": current["version"],
            "previous_session": report["previous_session"],
            "previous_version": previous["version"] if previous else None}
    if previous is None:
        return {**base, "status": "insufficient_history", "new": None,
                "continued": None, "exited": None, "changes": None, "streaks": None}
    if report["errors"] or previous["report"]["errors"]:
        return {**base, "status": "incomplete_data", "new": None,
                "continued": None, "exited": None, "changes": None, "streaks": None}
    rules = report.get("provenance", {}).get("rules_sha256")
    old_rules = previous["report"].get("provenance", {}).get("rules_sha256")
    if not rules or rules != old_rules:
        return {**base, "status": "incompatible_rules", "new": None,
                "continued": None, "exited": None, "changes": None, "streaks": None}
    now, before = by_security(report), by_security(previous["report"])
    added, continued, exited = sorted(now.keys() - before.keys()), sorted(now.keys() & before.keys()), sorted(before.keys() - now.keys())
    changes = [{"market": m, "code": c, "metrics": {
        k: {"previous": before[(m,c)].get(k), "current": now[(m,c)].get(k),
            "delta": (now[(m,c)][k] - before[(m,c)][k])
            if now[(m,c)].get(k) is not None and before[(m,c)].get(k) is not None else None}
        for k in METRICS}} for m,c in continued]
    counts = dict.fromkeys(now, 1)
    active = set(now)
    cursor, visited, baseline = current, set(), []
    while active:
        ds = cursor["report"]["previous_session"]
        if ds in visited:
            raise DataError("Cyclic previous-session result history")
        visited.add(ds)
        prior = store.official(ds)
        if (prior is None or prior["report"]["errors"] or
                prior["report"].get("provenance", {}).get("rules_sha256") != rules):
            break
        baseline.append(prior["version"])
        members = by_security(prior["report"])
        for key in active - members.keys():
            active.remove(key)  # A known non-selection ends the run exactly.
        for key in active:
            counts[key] += 1
        cursor = prior
    return {**base, "status": "complete", "new": [now[k] for k in added],
            "continued": [now[k] for k in continued], "exited": [before[k] for k in exited],
            "changes": changes, "baseline_versions": baseline,
            "streaks": [{"market": m, "code": c, "sessions": counts[(m,c)],
                         "exact": (m,c) not in active} for m,c in sorted(now)]}
