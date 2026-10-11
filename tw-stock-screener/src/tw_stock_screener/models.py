from dataclasses import dataclass, asdict
from datetime import date
import math


class DataError(ValueError):
    """Missing, inconsistent or unverified official data; never a passing signal."""


class NoData(DataError):
    """Official API explicitly reported no data for the requested date."""


@dataclass(frozen=True)
class Quote:
    date: date
    market: str
    code: str
    name: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None
    change: float | None

    def valid_prices(self):
        prices = (self.open, self.high, self.low, self.close)
        return (all(x is not None and math.isfinite(x) and x > 0 for x in prices)
                and self.low <= min(self.open, self.close)
                and self.high >= max(self.open, self.close))

    def valid_bar(self):
        return self.valid_prices() and self.volume is not None and self.volume >= 0

    @property
    def change_pct(self):
        if self.close is None or self.change is None:
            return None
        reference = self.close - self.change
        if not math.isfinite(reference) or reference <= 0:
            return None
        return 100 * self.change / reference

    def record(self):
        return {**asdict(self), "date": self.date.isoformat()}


@dataclass(frozen=True)
class Security:
    market: str
    code: str
    name: str
    cfi: str
    listed_on: date

    @property
    def ordinary(self):
        # ISO 10962: E=equities, S=common/ordinary shares. EP/ED/CE are excluded.
        return len(self.cfi) == 6 and self.cfi.startswith("ES")
