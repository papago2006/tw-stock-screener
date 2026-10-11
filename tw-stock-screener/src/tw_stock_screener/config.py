from copy import deepcopy
from pathlib import Path
import math
import yaml
from .models import DataError

DEFAULT = {
    "selection": {"top_n": 300, "volume_ratio": 3.2},
    "kd": {"period": 26, "k_smoothing": 3, "d_smoothing": 3,
           "initial": 50.0, "warmup_periods": 60},
    "history": {"months": 120, "daily_months": 6, "weekly_months": 24},
    "network": {"timeout": 30, "attempts": 3, "interval_seconds": 2.0,
                "latest_search_days": 20},
    "paths": {"cache": "data/market.sqlite3", "raw": "data/raw", "output": "output",
              "results": "data/results.sqlite3"},
}


def load_config(path=None):
    cfg = deepcopy(DEFAULT)
    base = Path.cwd()
    if path:
        path = Path(path).resolve()
        base = path.parent
        supplied = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(supplied, dict):
            raise DataError("YAML root must be a mapping")
        for section, values in supplied.items():
            if section not in cfg or not isinstance(values, dict):
                raise DataError(f"Unknown/invalid config section: {section}")
            for key, value in values.items():
                if key not in cfg[section]:
                    raise DataError(f"Unknown config key: {section}.{key}")
                cfg[section][key] = value
    for section, key in [("selection", "top_n"), ("kd", "period"),
                         ("kd", "k_smoothing"), ("kd", "d_smoothing"),
                         ("history", "months"), ("network", "attempts"),
                         ("history", "daily_months"), ("history", "weekly_months"),
                         ("network", "latest_search_days")]:
        v = cfg[section][key]
        if type(v) is not int or v <= 0:
            raise DataError(f"{section}.{key} must be a positive integer")
    w = cfg["kd"]["warmup_periods"]
    if type(w) is not int or w < 0:
        raise DataError("warmup_periods must be a non-negative integer")
    for section, key in [("selection", "volume_ratio"), ("network", "timeout"),
                         ("network", "interval_seconds"), ("kd", "initial")]:
        v = cfg[section][key]
        if type(v) not in (int, float) or not math.isfinite(v):
            raise DataError(f"{section}.{key} must be finite")
    if cfg["selection"]["volume_ratio"] <= 0 or cfg["network"]["timeout"] <= 0:
        raise DataError("volume_ratio/timeout must be positive")
    if cfg["network"]["interval_seconds"] < 0 or not 0 <= cfg["kd"]["initial"] <= 100:
        raise DataError("Invalid network interval or KD initial value")
    if cfg["history"]["months"] < cfg["kd"]["period"] + w + 1:
        raise DataError("History must cover monthly RSV, warmup and boundary month")
    cfg["paths"] = {k: str((base / v).resolve()) for k, v in cfg["paths"].items()}
    return cfg
