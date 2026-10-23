"""TreasuryDirect auction data: today's calendar and recent results.

Fields the feed does not provide stay None — bid-to-cover and high yield are
reported only when TreasuryDirect actually published them. Auction close
times are not in the feed; a config-level table of conventional close times
is attached, labeled "(typical)". Tails are NOT computed: that needs a 1pm
when-issued quote, which has no reliable free source.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import requests

from ..config import Config
from ..models import Auction
from .base import DEFAULT_TIMEOUT

UPCOMING_URL = "https://www.treasurydirect.gov/TA_WS/securities/upcoming"
AUCTIONED_URL = "https://www.treasurydirect.gov/TA_WS/securities/auctioned"


def _num(record: dict[str, Any], *names: str) -> float | None:
    for name in names:
        val = record.get(name)
        if val in (None, "", "null"):
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return None


def _text(record: dict[str, Any], *names: str) -> str | None:
    for name in names:
        val = record.get(name)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _auction_date(record: dict[str, Any]) -> str | None:
    raw = _text(record, "auctionDate")
    return raw[:10] if raw else None


def _to_auction(record: dict[str, Any], cfg: Config, *, is_result: bool) -> Auction:
    sec_type = _text(record, "securityType", "type")
    return Auction(
        security_type=sec_type,
        security_term=_text(record, "securityTerm", "term"),
        cusip=_text(record, "cusip"),
        auction_date=_auction_date(record),
        offering_amount=_num(record, "offeringAmount"),
        typical_close_time_et=cfg.auction_typical_times_et.get(sec_type or ""),
        high_yield=_num(record, "highYield", "highInvestmentRate") if is_result else None,
        high_discount_rate=_num(record, "highDiscountRate") if is_result else None,
        bid_to_cover=_num(record, "bidToCoverRatio") if is_result else None,
        total_accepted=_num(record, "totalAccepted") if is_result else None,
        is_result=is_result,
    )


def fetch_todays_auctions(session: requests.Session, cfg: Config, today: date) -> list[Auction]:
    resp = session.get(UPCOMING_URL, params={"format": "json"}, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    records = resp.json() or []
    return [
        _to_auction(r, cfg, is_result=False)
        for r in records
        if _auction_date(r) == today.isoformat()
    ]


def fetch_recent_results(session: requests.Session, cfg: Config, today: date) -> list[Auction]:
    """Results from auctions that cleared this week (excluding today)."""
    resp = session.get(AUCTIONED_URL, params={"format": "json", "days": 7}, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    records = resp.json() or []
    out: list[Auction] = []
    for r in records:
        d = _auction_date(r)
        if d is None or d >= today.isoformat():
            continue
        out.append(_to_auction(r, cfg, is_result=True))
    return out
