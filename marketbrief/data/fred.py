"""FRED client.

Uses the official API when FRED_API_KEY is set (recommended — stable,
documented), and falls back to the public fredgraph.csv endpoint otherwise
so the pipeline can run before a key is provisioned. Returns the last two
non-missing observations so the daily change is computable with honest
as-of dates.
"""

from __future__ import annotations

import csv
import io
import os
from datetime import date, timedelta

import requests

from .base import DEFAULT_TIMEOUT, RawQuote

API_URL = "https://api.stlouisfed.org/fred/series/observations"
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# How far back to ask for observations; generous enough to cover holidays,
# publication lags, and long weekends.
LOOKBACK_DAYS = 21


def _pair_to_quote(obs: list[tuple[str, float]], series: str) -> RawQuote:
    """Latest two (date, value) observations -> RawQuote."""
    if not obs:
        raise ValueError(f"FRED {series}: no usable observations")
    last_date, last_val = obs[-1]
    prev_date, prev_val = (obs[-2] if len(obs) >= 2 else (None, None))
    return RawQuote(
        value=last_val,
        prev_value=prev_val,
        asof=None,
        asof_date=last_date,
        prev_asof_date=prev_date,
    )


def _fetch_api(session: requests.Session, series: str, api_key: str) -> RawQuote:
    start = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    resp = session.get(
        API_URL,
        params={
            "series_id": series,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": start,
            "sort_order": "asc",
        },
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    rows = resp.json().get("observations", [])
    obs = [(r["date"], float(r["value"])) for r in rows if r.get("value") not in (None, "", ".")]
    return _pair_to_quote(obs, series)


def _fetch_csv(session: requests.Session, series: str) -> RawQuote:
    start = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    resp = session.get(CSV_URL, params={"id": series, "cosd": start}, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    reader = csv.reader(io.StringIO(resp.text))
    header = next(reader, None)
    if not header or len(header) < 2:
        raise ValueError(f"FRED csv {series}: unexpected header {header!r}")
    obs: list[tuple[str, float]] = []
    for row in reader:
        if len(row) < 2 or row[1] in (".", "", None):
            continue
        obs.append((row[0], float(row[1])))
    return _pair_to_quote(obs, series)


def fetch_series(session: requests.Session, series: str) -> RawQuote:
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if api_key:
        return _fetch_api(session, series, api_key)
    return _fetch_csv(session, series)
