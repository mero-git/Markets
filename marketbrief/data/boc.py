"""Bank of Canada Valet API client — GoC benchmark yields, daily official."""

from __future__ import annotations

import requests

from .base import DEFAULT_TIMEOUT, RawQuote

VALET_URL = "https://www.bankofcanada.ca/valet/observations/{series}/json"


def fetch_series(session: requests.Session, series: str) -> RawQuote:
    resp = session.get(VALET_URL.format(series=series), params={"recent": 10}, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    rows = resp.json().get("observations", [])
    obs: list[tuple[str, float]] = []
    for row in rows:
        cell = row.get(series) or {}
        val = cell.get("v")
        if val in (None, ""):
            continue
        obs.append((row.get("d", ""), float(val)))
    obs.sort(key=lambda x: x[0])
    if not obs:
        raise ValueError(f"BoC Valet {series}: no observations")
    last_d, last_v = obs[-1]
    prev_d, prev_v = (obs[-2] if len(obs) >= 2 else (None, None))
    return RawQuote(value=last_v, prev_value=prev_v, asof=None, asof_date=last_d, prev_asof_date=prev_d)
