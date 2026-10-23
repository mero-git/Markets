"""Snapshot builder: orchestrates every fetch, validates, derives, reports.

This is the only producer of :class:`MarketSnapshot`. Guarantees:

- every fetch is individually wrapped — one bad ticker never kills the run;
- every value passes sanity + staleness checks or is recorded as a rejection;
- changes and curve spreads are computed HERE, so the generation model never
  performs arithmetic;
- failures and rejections are first-class output, feeding the health gate.
"""

from __future__ import annotations

import logging
from datetime import datetime

import requests

from ..config import Config, DerivedSpec, InstrumentSpec
from ..health import assess_health
from ..models import (
    Auction,
    DataPoint,
    FetchFailure,
    Freshness,
    MarketSnapshot,
    Rejection,
)
from ..schedule import is_bond_market_open, is_send_day, local_trading_date
from . import boc, foreign, fred, treasury, yahoo
from .base import RawQuote, SourceError, fetch_with_retries, make_session, utc_now
from .sanity import check_correlated_pair, validate

log = logging.getLogger(__name__)

_YIELD_CLASSES = {"yield_pct", "real_yield_pct"}


def _dispatch(session: requests.Session, spec: InstrumentSpec) -> RawQuote:
    if spec.source == "yahoo":
        return yahoo.fetch_quote(session, spec.symbol)
    if spec.source == "fred":
        return fred.fetch_series(session, spec.symbol)
    if spec.source == "boc":
        return boc.fetch_series(session, spec.symbol)
    if spec.source == "bundesbank":
        return foreign.fetch_bund(session, spec.symbol)
    if spec.source == "mof_jgb":
        return foreign.fetch_jgb(session, spec.symbol)
    if spec.source == "boe":
        return foreign.fetch_gilt(session, spec.symbol)
    raise SourceError(f"unknown source {spec.source!r} for {spec.key}")


def _apply_scale(quote: RawQuote, scale: float) -> RawQuote:
    if scale == 1.0:
        return quote
    return RawQuote(
        value=quote.value * scale,
        prev_value=quote.prev_value * scale if quote.prev_value is not None else None,
        asof=quote.asof,
        asof_date=quote.asof_date,
        prev_asof_date=quote.prev_asof_date,
        week_value=quote.week_value * scale if quote.week_value is not None else None,
        week_asof_date=quote.week_asof_date,
    )


def _delta(value: float, base: float | None, spec: InstrumentSpec
           ) -> tuple[float | None, str | None, float | None]:
    """(change, change_unit, change_pct) for a value against a baseline."""
    if base is None:
        return None, None, None
    if spec.cls in _YIELD_CLASSES:
        return round((value - base) * 100.0, 2), "bp", None   # % -> bp
    if spec.unit == "bp":
        return round(value - base, 2), "bp", None
    pct = round((value / base - 1.0) * 100.0, 2) if base != 0 else None
    return round(value - base, 6), spec.unit, pct


def _to_point(quote: RawQuote, spec: InstrumentSpec, now: datetime) -> DataPoint:
    change, change_unit, change_pct = _delta(quote.value, quote.prev_value, spec)
    change_week, week_unit, change_week_pct = _delta(quote.value, quote.week_value, spec)
    change_unit = change_unit or week_unit
    return DataPoint(
        key=spec.key,
        label=spec.label,
        section=spec.section,
        value=quote.value,
        unit=spec.unit,
        source=f"{spec.source}:{spec.symbol}",
        fetched_at=now,
        asof=quote.asof,
        asof_date=quote.asof_date,
        freshness=Freshness(spec.freshness),
        prev_value=quote.prev_value,
        prev_asof_date=quote.prev_asof_date,
        change=change,
        change_unit=change_unit,
        change_pct=change_pct,
        week_value=quote.week_value,
        week_asof_date=quote.week_asof_date,
        change_week=change_week,
        change_week_pct=change_week_pct,
        note=spec.note,
    )


def _derive(spec: DerivedSpec, points: dict[str, DataPoint], now: datetime) -> DataPoint | None:
    a, b = points.get(spec.a), points.get(spec.b)
    if a is None or b is None:
        log.info("derived %s skipped: missing leg(s)", spec.key)
        return None
    if spec.kind != "yield_spread_bp":
        log.warning("derived %s skipped: unknown kind %s", spec.key, spec.kind)
        return None
    value = round((a.value - b.value) * 100.0, 1)
    change = None
    if a.change is not None and b.change is not None and a.change_unit == b.change_unit == "bp":
        change = round(a.change - b.change, 1)
    change_week = None
    if a.change_week is not None and b.change_week is not None:
        change_week = round(a.change_week - b.change_week, 1)
    week_value = None
    if a.week_value is not None and b.week_value is not None:
        week_value = round((a.week_value - b.week_value) * 100.0, 1)
    asof_dates = [d for d in (a.asof_date, b.asof_date) if d]
    return DataPoint(
        key=spec.key,
        label=spec.label,
        section=spec.section,
        value=value,
        unit="bp",
        source=f"derived:{spec.a}-{spec.b}",
        fetched_at=now,
        asof=min(x for x in (a.asof, b.asof) if x is not None) if (a.asof or b.asof) else None,
        asof_date=min(asof_dates) if asof_dates else None,
        freshness=Freshness.DAILY if a.freshness == b.freshness == Freshness.DAILY else Freshness.LIVE,
        change=change,
        change_unit="bp" if (change is not None or change_week is not None) else None,
        week_value=week_value,
        week_asof_date=a.week_asof_date or b.week_asof_date,
        change_week=change_week,
        note=spec.note,
    )


def build_snapshot(cfg: Config, *, session: requests.Session | None = None) -> MarketSnapshot:
    session = session or make_session()
    now = utc_now()
    trading_date = local_trading_date(cfg, now)

    points: list[DataPoint] = []
    failures: list[FetchFailure] = []
    rejections: list[Rejection] = []

    for spec in cfg.instruments:
        what = f"{spec.source}:{spec.symbol}"
        try:
            quote = fetch_with_retries(lambda s=spec: _dispatch(session, s), what=what)
        except SourceError as exc:
            log.error("fetch failed %s: %s", spec.key, exc)
            failures.append(FetchFailure(key=spec.key, source=what, reason=str(exc), fetched_at=now))
            continue
        quote = _apply_scale(quote, spec.scale)
        result = validate(quote, spec, cfg, now)
        if not result.ok:
            log.warning("rejected %s: %s", spec.key, result.reason)
            rejections.append(Rejection(
                key=spec.key, source=what, reason=result.reason or "unknown",
                value=quote.value, prev_value=quote.prev_value, fetched_at=now,
            ))
            continue
        points.append(_to_point(quote, spec, now))

    # Cross-instrument screen: legs that normally move together must agree.
    by_key = {p.key: p for p in points}
    for pair in cfg.correlated_pairs:
        a, b = by_key.get(pair.a), by_key.get(pair.b)
        if a is None or b is None:
            continue
        a_pct = a.change_week_pct if pair.basis == "week" else a.change_pct
        b_pct = b.change_week_pct if pair.basis == "week" else b.change_pct
        result = check_correlated_pair(pair, a_pct, b_pct)
        if result.ok:
            continue
        log.warning("correlated-pair rejection %s/%s: %s", pair.a, pair.b, result.reason)
        for point in (a, b):
            rejections.append(Rejection(
                key=point.key, source=point.source, reason=result.reason or "pair divergence",
                value=point.value, prev_value=point.prev_value, fetched_at=now))
            points.remove(point)
        by_key.pop(pair.a, None)
        by_key.pop(pair.b, None)

    for dspec in cfg.derived:
        derived = _derive(dspec, by_key, now)
        if derived is not None:
            points.append(derived)

    auctions_today: list[Auction] = []
    auction_results: list[Auction] = []
    try:
        auctions_today = fetch_with_retries(
            lambda: treasury.fetch_todays_auctions(session, cfg, trading_date),
            what="treasurydirect:upcoming")
    except SourceError as exc:
        failures.append(FetchFailure(key="auctions_today", source="treasurydirect:upcoming",
                                     reason=str(exc), fetched_at=now))
    try:
        auction_results = fetch_with_retries(
            lambda: treasury.fetch_recent_results(session, cfg, trading_date),
            what="treasurydirect:auctioned")
    except SourceError as exc:
        failures.append(FetchFailure(key="auction_results", source="treasurydirect:auctioned",
                                     reason=str(exc), fetched_at=now))

    health = assess_health(
        attempted=len(cfg.instruments),
        points=[p for p in points if not p.source.startswith("derived:")],
        failures=[f for f in failures if f.key not in ("auctions_today", "auction_results")],
        rejections=rejections,
        cfg=cfg.health,
    )

    return MarketSnapshot(
        generated_at=now,
        trading_date=trading_date.isoformat(),
        nyse_open_today=is_send_day(cfg, trading_date),
        sifma_open_today=is_bond_market_open(cfg, trading_date),
        points=points,
        auctions_today=auctions_today,
        auction_results=auction_results,
        failures=failures,
        rejections=rejections,
        health=health,
    )
