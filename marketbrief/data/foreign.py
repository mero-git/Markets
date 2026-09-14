"""Foreign government 10Y yields from official national sources.

All three are once-a-day official prints (prior close), timestamped by
observation date — never presented as live:

- Bund 10Y  — Deutsche Bundesbank statistics API (daily listed federal
  securities yield)
- JGB 10Y   — Japan Ministry of Finance daily interest-rate CSV
- Gilt 10Y  — Bank of England IADB, 10Y nominal zero-coupon yield
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timedelta

import requests

from .base import DEFAULT_TIMEOUT, RawQuote

BUNDESBANK_URL = "https://api.statistiken.bundesbank.de/rest/data/BBSIS/{series}"
MOF_JGB_URL = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
BOE_IADB_URL = "https://www.bankofengland.co.uk/boeapps/iadb/fromshowcolumns.asp"

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _last_two(obs: list[tuple[str, float]], what: str) -> RawQuote:
    obs = sorted(obs, key=lambda x: x[0])
    if not obs:
        raise ValueError(f"{what}: no observations parsed")
    last_d, last_v = obs[-1]
    prev_d, prev_v = (obs[-2] if len(obs) >= 2 else (None, None))
    return RawQuote(value=last_v, prev_value=prev_v, asof=None, asof_date=last_d, prev_asof_date=prev_d)


def fetch_bund(session: requests.Session, series: str) -> RawQuote:
    """Daily Bund yield from the Bundesbank REST API (CSV flavour)."""
    resp = session.get(
        BUNDESBANK_URL.format(series=series),
        params={"lastNObservations": 10, "format": "csv", "lang": "en"},
        headers={"Accept": "text/csv"},
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    obs: list[tuple[str, float]] = []
    for row in csv.reader(io.StringIO(resp.text), delimiter=";"):
        if not row or not _ISO_DATE.match(row[0].strip()):
            continue
        raw = row[1].strip().replace(",", ".") if len(row) > 1 else ""
        try:
            obs.append((row[0].strip(), float(raw)))
        except ValueError:
            continue
    return _last_two(obs, f"bundesbank {series}")


def fetch_jgb(session: requests.Session, tenor: str) -> RawQuote:
    """Daily JGB yield for ``tenor`` (e.g. "10Y") from the MoF CSV."""
    resp = session.get(MOF_JGB_URL, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    rows = list(csv.reader(io.StringIO(resp.content.decode("utf-8", errors="replace"))))
    header_idx, col = None, None
    for i, row in enumerate(rows):
        cells = [c.strip() for c in row]
        if "Date" in cells and tenor in cells:
            header_idx, col = i, cells.index(tenor)
            break
    if header_idx is None or col is None:
        raise ValueError(f"MoF JGB csv: no header row with Date and {tenor}")
    obs: list[tuple[str, float]] = []
    for row in rows[header_idx + 1:]:
        if len(row) <= col:
            continue
        raw_date, raw_val = row[0].strip(), row[col].strip()
        if not raw_date or raw_val in ("", "-"):
            continue
        try:
            d = datetime.strptime(raw_date, "%Y/%m/%d").date().isoformat()
            obs.append((d, float(raw_val)))
        except ValueError:
            continue
    return _last_two(obs[-10:], "mof jgb")


def fetch_gilt(session: requests.Session, series: str) -> RawQuote:
    """Daily gilt yield from the BoE IADB CSV export (publishes with a lag)."""
    start = date.today() - timedelta(days=21)
    resp = session.get(
        BOE_IADB_URL,
        params={
            "csv.x": "yes",
            "Datefrom": start.strftime("%d/%b/%Y"),
            "Dateto": "now",
            "SeriesCodes": series,
            "CSVF": "TN",
            "UsingCodes": "Y",
            "VPD": "Y",
            "VFD": "N",
        },
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    obs: list[tuple[str, float]] = []
    for row in csv.reader(io.StringIO(resp.text)):
        if len(row) < 2:
            continue
        try:
            d = datetime.strptime(row[0].strip(), "%d %b %Y").date().isoformat()
            obs.append((d, float(row[1])))
        except ValueError:
            continue
    return _last_two(obs, f"boe {series}")
