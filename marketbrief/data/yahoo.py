"""Yahoo Finance chart-API client.

Uses the public v8 chart endpoint directly (the same one yfinance wraps) so
behaviour is predictable and the dependency surface stays small. Yahoo is an
unofficial source — this client is deliberately paranoid about the shape of
the response, and everything it returns still goes through sanity checks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from .base import DEFAULT_TIMEOUT, RawQuote

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
    """Last price and previous close for ``symbol``.

    ``value`` is the current/last regular-market price with its timestamp;
    ``prev_value`` is Yahoo's previousClose when present, otherwise the last
    daily close from a session strictly before the current one.
    """
    resp = session.get(
        CHART_URL.format(symbol=symbol),
        params={"range": "10d", "interval": "1d", "includePrePost": "false"},
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

    prev = meta.get("previousClose")
    prev_date: str | None = None
    if prev is None:
        # Fall back to daily bars: last close from a session before the
        # current quote's date. chartPreviousClose is NOT used — it is the
        # close before the requested range, which can be ~10 days stale.
        bars = _daily_closes(result)
        earlier = [(t, c) for t, c in bars if t.date() < asof.date()]
        if earlier:
            prev_ts, prev = earlier[-1]
            prev_date = prev_ts.date().isoformat()
    return RawQuote(
        value=float(price),
        prev_value=float(prev) if prev is not None else None,
        asof=asof,
        asof_date=None,
        prev_asof_date=prev_date,
    )
