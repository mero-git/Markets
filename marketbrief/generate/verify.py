"""Numeric post-validation of generated text — the last wall before send.

Architecture note: the email's numeric tables are rendered by code straight
from the snapshot, so the model only writes narrative (TLDR, per-section
commentary, news lines). Even so, every numeral in that narrative must trace
to the data payload. This module builds the set of allowed number tokens
from the snapshot + news bundle and rejects any generated numeral outside
it. A violation fails the run — the email is not sent.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..models import Auction, MarketSnapshot
from ..news.search import NewsBundle

_NUM_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?")
_URL = re.compile(r"https?://\S+")


def _clean(tok: str) -> str:
    """Canonical form of a number token: '4.960' == '4.96', '7,695.75' == '7695.75'."""
    try:
        return "{:g}".format(float(tok.replace(",", "")))
    except (ValueError, OverflowError):
        return tok


def _numeric_variants(x: float) -> set[str]:
    """Every plausible textual rendering of a payload number."""
    out: set[str] = set()
    for v in {x, abs(x), round(x, 0), round(abs(x), 0)}:
        for fmt in ("{:.0f}", "{:.1f}", "{:.2f}", "{:.3f}", "{:.4f}", "{:g}"):
            try:
                out.add(_clean(fmt.format(v)))
            except (ValueError, OverflowError):
                continue
    # Billions/millions shorthand for large sizes ("$22bn")
    if abs(x) >= 1e6:
        for div in (1e9, 1e6):
            for fmt in ("{:.0f}", "{:.1f}", "{:.2f}"):
                out.add(_clean(fmt.format(abs(x) / div)))
    return out


def _tokens_in_text(text: str) -> set[str]:
    return {_clean(t) for t in _NUM_TOKEN.findall(text)}


def _auction_numbers(a: Auction) -> Iterable[float]:
    for v in (a.offering_amount, a.high_yield, a.high_discount_rate,
              a.bid_to_cover, a.total_accepted):
        if v is not None:
            yield v


def build_allowed_numbers(snapshot: MarketSnapshot, news: NewsBundle | None) -> set[str]:
    allowed: set[str] = set()

    for p in snapshot.points:
        for v in (p.value, p.prev_value, p.change, p.change_pct,
                  p.week_value, p.change_week, p.change_week_pct):
            if v is not None:
                allowed |= _numeric_variants(v)
        # Numbers embedded in labels/notes/dates ("S&P 500", "2s10s", obs dates)
        for s in (p.label, p.note, p.asof_date, p.prev_asof_date,
                  p.week_asof_date, p.unit):
            if s:
                allowed |= _tokens_in_text(s)

    for a in [*snapshot.auctions_today, *snapshot.auction_results]:
        for v in _auction_numbers(a):
            allowed |= _numeric_variants(v)
        for s in (a.security_term, a.security_type, a.auction_date,
                  a.typical_close_time_et):
            if s:
                allowed |= _tokens_in_text(s)

    allowed |= _tokens_in_text(snapshot.trading_date)

    if news is not None:
        for item in news.items:
            allowed |= _tokens_in_text(f"{item.headline} {item.detail} {item.source_name}")
        for c in [*news.calendar, *news.cb_speakers]:
            allowed |= _tokens_in_text(f"{c.time_et or ''} {c.item} {c.consensus or ''}")

    return allowed


def find_violations(text: str, allowed: set[str]) -> list[str]:
    """Number tokens in ``text`` that do not trace to the payload."""
    text = _URL.sub(" ", text)  # source URLs carry meaningless digits
    return sorted(t for t in _tokens_in_text(text) if t not in allowed)


def verify_narrative(fields: dict[str, str], snapshot: MarketSnapshot,
                     news: NewsBundle | None) -> dict[str, list[str]]:
    """Check every generated text field; return {field: violations} for failures."""
    allowed = build_allowed_numbers(snapshot, news)
    failures: dict[str, list[str]] = {}
    for name, text in fields.items():
        if not text:
            continue
        bad = find_violations(text, allowed)
        if bad:
            failures[name] = bad
    return failures
