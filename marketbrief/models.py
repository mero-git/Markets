"""Core data types shared across the data, generation, and delivery layers.

Every number that can ever appear in an outgoing brief exists first as a
:class:`DataPoint` here, carrying its source, fetch timestamp, and data
as-of timestamp. Anything that cannot be traced back to one of these is,
by construction, not allowed into the output.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any


class Freshness(str, Enum):
    """How current a source is expected to be."""

    LIVE = "live"      # trades ~24h (futures, FX) or is a same-day session value
    DAILY = "daily"    # official once-a-day series (FRED, BoC, MoF, ...)


@dataclass(slots=True)
class DataPoint:
    """A single fetched market value with full provenance."""

    key: str
    label: str
    section: str
    value: float
    unit: str
    source: str                      # e.g. "yahoo:ES=F", "fred:DGS10"
    fetched_at: datetime             # UTC wall clock of the fetch
    asof: datetime | None            # data timestamp (UTC) when the source gives one
    asof_date: str | None            # ISO date for daily official series
    freshness: Freshness
    prev_value: float | None = None
    prev_asof_date: str | None = None
    change: float | None = None      # value - prev_value, in change_unit
    change_unit: str | None = None   # "bp", "%", or unit of the level
    change_pct: float | None = None  # percent change where meaningful
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["freshness"] = self.freshness.value
        for k in ("fetched_at", "asof"):
            if d[k] is not None:
                d[k] = d[k].isoformat()
        return d


@dataclass(slots=True)
class FetchFailure:
    """A fetch that returned nothing usable. The field is omitted downstream."""

    key: str
    source: str
    reason: str
    fetched_at: datetime

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["fetched_at"] = self.fetched_at.isoformat()
        return d


@dataclass(slots=True)
class Rejection:
    """A fetched value that failed a sanity or staleness check."""

    key: str
    source: str
    reason: str
    value: float | None
    prev_value: float | None
    fetched_at: datetime

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["fetched_at"] = self.fetched_at.isoformat()
        return d


@dataclass(slots=True)
class Auction:
    """A Treasury auction — upcoming or with results. Fields absent from the
    TreasuryDirect feed stay None and are omitted downstream, never guessed."""

    security_type: str | None
    security_term: str | None
    cusip: str | None
    auction_date: str | None
    offering_amount: float | None          # USD
    typical_close_time_et: str | None      # convention, labeled "(typical)"
    high_yield: float | None = None        # % — results only
    high_discount_rate: float | None = None
    bid_to_cover: float | None = None
    total_accepted: float | None = None
    is_result: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class HealthReport:
    """Fail-closed decision input: how healthy was this run's data fetch."""

    attempted: int
    fetched: int
    failed: int
    rejected: int
    critical_missing: list[str]
    failure_ratio: float
    degraded: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MarketSnapshot:
    """Everything the generation layer is allowed to know about markets."""

    generated_at: datetime
    trading_date: str                # ISO date in ET
    nyse_open_today: bool
    sifma_open_today: bool | None
    points: list[DataPoint]
    auctions_today: list[Auction]
    auction_results: list[Auction]
    failures: list[FetchFailure]
    rejections: list[Rejection]
    health: HealthReport

    def point(self, key: str) -> DataPoint | None:
        for p in self.points:
            if p.key == key:
                return p
        return None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MarketSnapshot":
        def dt(v: str | None) -> datetime | None:
            return datetime.fromisoformat(v) if v else None

        def point(p: dict[str, Any]) -> DataPoint:
            p = dict(p)
            p["freshness"] = Freshness(p["freshness"])
            p["fetched_at"] = dt(p["fetched_at"])
            p["asof"] = dt(p.get("asof"))
            return DataPoint(**p)

        def failure(f: dict[str, Any]) -> FetchFailure:
            f = dict(f)
            f["fetched_at"] = dt(f["fetched_at"])
            return FetchFailure(**f)

        def rejection(r: dict[str, Any]) -> Rejection:
            r = dict(r)
            r["fetched_at"] = dt(r["fetched_at"])
            return Rejection(**r)

        return cls(
            generated_at=dt(d["generated_at"]),
            trading_date=d["trading_date"],
            nyse_open_today=d["nyse_open_today"],
            sifma_open_today=d.get("sifma_open_today"),
            points=[point(p) for p in d["points"]],
            auctions_today=[Auction(**a) for a in d["auctions_today"]],
            auction_results=[Auction(**a) for a in d["auction_results"]],
            failures=[failure(f) for f in d["failures"]],
            rejections=[rejection(r) for r in d["rejections"]],
            health=HealthReport(**d["health"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "trading_date": self.trading_date,
            "nyse_open_today": self.nyse_open_today,
            "sifma_open_today": self.sifma_open_today,
            "points": [p.to_dict() for p in self.points],
            "auctions_today": [a.to_dict() for a in self.auctions_today],
            "auction_results": [a.to_dict() for a in self.auction_results],
            "failures": [f.to_dict() for f in self.failures],
            "rejections": [r.to_dict() for r in self.rejections],
            "health": self.health.to_dict(),
        }
