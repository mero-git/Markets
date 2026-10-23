"""Yahoo Finance chart-API client.

Uses the public v8 chart endpoint directly (the same one yfinance wraps) so
behaviour is predictable and the dependency surface stays small. Yahoo is an
unofficial source, so this client is deliberately paranoid about the shape of
the response, and everything it returns still goes through sanity checks.

For a weekly note the headline figure is the move since the prior Friday's
close, so daily bars are always requested and the week-ago close is taken
from them rather than inferred.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from .base import DEFAULT_TIMEOUT, RawQuote, pick_week_ago

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def _daily_closes(result: dict[str, Any]) -> list[tuple[datetime, float]]:
    """(bar timestamp UTC, close) for each daily bar with a real close."""
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    out: list[tuple[datetime, float]] = []
    for ts, close in zip(timestamps, closes):
        if ts is None or close is None:
            continue
        out.append((datetime.fromtimestamp(ts, tz=timezone.utc), float(close)))
    return out


def fetch_quote(session: requests.Session, symbol: str) -> RawQuote:
    """Last price, previous close, and the close one week back.

    ``value`` is the current or last regular-market price with its timestamp.
    ``prev_value`` is Yahoo's previousClose when present, otherwise the last
    daily close from a session strictly before the current one. ``week_value``
    is the last daily close on or before six days before the quote date.
    """
    resp = session.get(
        CHART_URL.format(symbol=symbol),
        params={"range": "1mo", "interval": "1d", "includePrePost": "false"},
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise ValueError(f"yahoo error for {symbol}: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise ValueError(f"yahoo returned no result for {symbol}")
    result = results[0]
    meta = result.get("meta") or {}

    price = meta.get("regularMarketPrice")
    ts = meta.get("regularMarketTime")
    if price is None or ts is None:
        raise ValueError(f"yahoo meta missing price/time for {symbol}")
    asof = datetime.fromtimestamp(int(ts), tz=timezone.utc)

    bars = _daily_closes(result)
    earlier = [(t, c) for t, c in bars if t.date() < asof.date()]

    prev = meta.get("previousClose")
    prev_date: str | None = None
    if prev is None:
        # chartPreviousClose is NOT used: it is the close before the requested
        # range, which can be a month stale.
        if earlier:
            prev_ts, prev = earlier[-1]
            prev_date = prev_ts.date().isoformat()

    week_date, week_value = pick_week_ago(
        [(t.date().isoformat(), c) for t, c in earlier], asof.date().isoformat())

    return RawQuote(
        value=float(price),
        prev_value=float(prev) if prev is not None else None,
        asof=asof,
        asof_date=None,
        prev_asof_date=prev_date,
        week_value=week_value,
        week_asof_date=week_date,
    )
