"""Outlook-safe email rendering.

Table-based layout, fully inline CSS, no flexbox, grid, or media queries,
with a plain-text alternative part. All numeric tables are rendered here,
deterministically, from the snapshot. The model never touches them.

House style is restrained institutional research: one accent colour, hairline
rules, generous white space, tabular figures right aligned, and no decoration
that does not carry information. Sections with no data and no commentary are
omitted entirely rather than printing an empty header.
"""

from __future__ import annotations

import html
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from ..config import Config
from ..generate.generator import BriefNarrative
from ..models import Auction, DataPoint, MarketSnapshot
from ..news.search import NewsBundle

_ET = ZoneInfo("America/New_York")

# --- palette ---------------------------------------------------------------
ACCENT = "#A6192E"        # restrained crimson, the single accent
INK = "#1A1A1A"
INK_SOFT = "#4A4A4A"
MUTED = "#8C8C8C"
RULE = "#E4E2DE"
PAGE = "#F2F1EE"
UP = "#15713C"
DOWN = "#B3261E"

SANS = ("font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;")

_H = (SANS + "font-size:11px;letter-spacing:1.6px;text-transform:uppercase;"
      f"color:{ACCENT};font-weight:bold;padding:0 0 7px 0;margin:0;")
_BODY_TXT = (SANS + f"font-size:13px;line-height:20px;color:{INK_SOFT};")
_COL_HEAD = (SANS + f"font-size:9px;letter-spacing:1px;text-transform:uppercase;"
             f"color:{MUTED};padding:0 0 5px 0;font-weight:normal;")
_CELL = SANS + f"font-size:13px;color:{INK};padding:5px 0;"
_PAD = "padding:0 32px;"

# Display order inside each section
_ORDER = {
    "ca_rates": ["boc_target", "corra", "goc_2y", "goc_5y", "goc_10y", "goc_30y",
                 "goc_2s10s", "goc_5s30s", "ca_us_2y", "ca_us_10y"],
    "us_rates": ["ust_2y", "ust_5y", "ust_10y", "ust_30y", "ust_2s10s",
                 "ust_5s30s", "breakeven_10y"],
    "credit": ["ig_oas", "hy_oas"],
    "fx": ["usdcad", "dxy", "eurusd", "usdjpy"],
    "commodities": ["wti", "brent", "natgas", "gold", "copper"],
    "equities": ["tsx", "spx", "nasdaq", "dow"],
    "volatility": ["move", "vix"],
}


# --- value formatting ------------------------------------------------------

def _level(p: DataPoint) -> str:
    if p.unit == "%":
        return f"{p.value:.2f}%"
    if p.unit == "bp":
        return f"{p.value:.0f} bp"
    if p.unit == "rate":
        return f"{p.value:.4f}" if abs(p.value) < 20 else f"{p.value:.2f}"
    if abs(p.value) >= 1000:
        return f"{p.value:,.0f}"
    return f"{p.value:,.2f}"


def _bp(x: float) -> str:
    return f"{x:+.0f} bp" if abs(x) >= 1 else f"{x:+.1f} bp"


def _change(value: float | None, pct: float | None, unit: str | None) -> str:
    if value is None and pct is None:
        return "n/a"
    if unit == "bp" and value is not None:
        return _bp(value)
    if pct is not None:
        return f"{pct:+.2f}%"
    return f"{value:+,.2f}"


def _week(p: DataPoint) -> str:
    return _change(p.change_week, p.change_week_pct, p.change_unit)


def _day(p: DataPoint) -> str:
    return _change(p.change, p.change_pct, p.change_unit)


def _dir_colour(value: float | None, pct: float | None) -> str:
    x = value if value is not None else pct
    if x is None or x == 0:
        return MUTED
    return UP if x > 0 else DOWN


def _plain_change(p: DataPoint) -> str:
    return f"{_week(p)} wk"


# --- building blocks -------------------------------------------------------

def _section_head(title: str) -> str:
    return (f'<tr><td style="{_PAD}padding-top:26px;">'
            f'<div style="{_H}">{html.escape(title)}</div>'
            f'<div style="height:2px;background-color:{ACCENT};width:28px;'
            'font-size:0;line-height:0;">&nbsp;</div>'
            f'<div style="height:1px;background-color:{RULE};font-size:0;'
            'line-height:0;margin-top:-2px;">&nbsp;</div></td></tr>')


def _comment(text: str | None) -> str:
    if not text:
        return ""
    return (f'<tr><td style="{_PAD}padding-top:12px;">'
            f'<div style="{_BODY_TXT}">{html.escape(text)}</div></td></tr>')


def _table(points: list[DataPoint]) -> str:
    if not points:
        return ""
    rows = [
        '<tr>'
        f'<td style="{_COL_HEAD}">&nbsp;</td>'
        f'<td style="{_COL_HEAD}text-align:right;">Level</td>'
        f'<td style="{_COL_HEAD}text-align:right;padding-left:18px;">1 week</td>'
        f'<td style="{_COL_HEAD}text-align:right;padding-left:14px;">1 day</td>'
        '</tr>'
    ]
    for p in points:
        wk_colour = _dir_colour(p.change_week, p.change_week_pct)
        rows.append(
            f'<tr>'
            f'<td style="{_CELL}border-top:1px solid {RULE};">{html.escape(p.label)}</td>'
            f'<td style="{_CELL}border-top:1px solid {RULE};text-align:right;'
            f'font-weight:bold;white-space:nowrap;">{_level(p)}</td>'
            f'<td style="{_CELL}border-top:1px solid {RULE};text-align:right;'
            f'padding-left:18px;color:{wk_colour};font-weight:bold;'
            f'white-space:nowrap;">{_week(p)}</td>'
            f'<td style="{_CELL}border-top:1px solid {RULE};text-align:right;'
            f'padding-left:14px;color:{MUTED};font-size:11px;'
            f'white-space:nowrap;">{_day(p)}</td>'
            f'</tr>')
    return (f'<tr><td style="{_PAD}padding-top:14px;">'
            '<table role="presentation" width="100%" cellpadding="0" '
            'cellspacing="0" border="0">' + "".join(rows) + "</table></td></tr>")


def _note_line(text: str) -> str:
    return (f'<tr><td style="{_PAD}padding-top:10px;">'
            f'<div style="{SANS}font-size:11px;line-height:16px;color:{MUTED};">'
            f'{html.escape(text)}</div></td></tr>')


def _ordered(snapshot: MarketSnapshot, section: str) -> list[DataPoint]:
    pts = [p for p in snapshot.points if p.section == section]
    order = _ORDER.get(section)
    if order:
        rank = {k: i for i, k in enumerate(order)}
        pts.sort(key=lambda p: rank.get(p.key, 99))
    return pts


def _auction_line(a: Auction) -> str:
    bits = [x for x in (a.security_term, a.security_type) if x]
    line = " ".join(bits) or "Security"
    if a.offering_amount:
        line += f", ${a.offering_amount / 1e9:.0f}bn"
    if a.is_result:
        if a.high_yield is not None:
            line += f" at {a.high_yield:.3f}%"
        elif a.high_discount_rate is not None:
            line += f" at {a.high_discount_rate:.3f}%"
        if a.bid_to_cover is not None:
            line += f", {a.bid_to_cover:.2f}x cover"
        if a.auction_date:
            line += f" ({a.auction_date})"
    elif a.typical_close_time_et:
        line += f", closes about {a.typical_close_time_et} ET"
    return line


# --- the note --------------------------------------------------------------

def render_email(cfg: Config, snapshot: MarketSnapshot, narrative: BriefNarrative,
                 news: NewsBundle, *, tier: str) -> tuple[str, str, str]:
    """Return (subject, html, plain_text)."""
    week_end = datetime.fromisoformat(snapshot.trading_date)
    nice_date = week_end.strftime("%B %-d, %Y")
    subject = f"{cfg.email.title}: week ending {week_end.strftime('%b %-d')}"
    if snapshot.health.degraded:
        subject = "[DEGRADED] " + subject

    parts: list[str] = []
    text: list[str] = [f"{cfg.email.title.upper()}", f"Week ending {nice_date}", ""]

    # Masthead
    parts.append(
        f'<tr><td style="background-color:{ACCENT};height:3px;font-size:0;'
        'line-height:0;">&nbsp;</td></tr>')
    parts.append(
        f'<tr><td style="{_PAD}padding-top:26px;padding-bottom:18px;'
        f'border-bottom:1px solid {RULE};">'
        f'<div style="{SANS}font-size:22px;font-weight:bold;color:{INK};'
        f'letter-spacing:-0.3px;">{html.escape(cfg.email.title)}</div>'
        f'<div style="{SANS}font-size:11px;color:{MUTED};padding-top:6px;'
        'letter-spacing:0.5px;">WEEK ENDING ' +
        html.escape(nice_date.upper()) + "</div></td></tr>")

    if snapshot.health.degraded:
        parts.append(
            f'<tr><td style="{_PAD}padding-top:16px;">'
            f'<div style="background-color:#FBEAEA;border-left:3px solid {DOWN};'
            f'padding:10px 14px;{SANS}font-size:12px;color:{DOWN};">'
            "Degraded run. Data quality below threshold, so this was not "
            "distributed beyond the sender.</div></td></tr>")
        text += ["** DEGRADED RUN, not distributed **", ""]

    # --- The week in three ---
    if narrative.three_points:
        items = []
        for i, line in enumerate(narrative.three_points, start=1):
            items.append(
                '<tr>'
                f'<td valign="top" style="{SANS}font-size:13px;font-weight:bold;'
                f'color:{ACCENT};padding:0 12px 10px 0;width:16px;">{i}</td>'
                f'<td valign="top" style="{SANS}font-size:14px;line-height:21px;'
                f'color:{INK};padding:0 0 10px 0;">{html.escape(line)}</td>'
                '</tr>')
        parts.append(_section_head("The week in three"))
        parts.append(
            f'<tr><td style="{_PAD}padding-top:14px;">'
            '<table role="presentation" width="100%" cellpadding="0" '
            'cellspacing="0" border="0">' + "".join(items) + "</table></td></tr>")
        text.append("THE WEEK IN THREE")
        text += [f"  {i}. {line}" for i, line in enumerate(narrative.three_points, 1)]
        text.append("")

    def plain_block(title: str, comment: str | None, pts: list[DataPoint]) -> None:
        # extend, never +=: an augmented assignment would rebind `text` local.
        text.append(title.upper())
        if comment:
            text.append(comment)
        text.extend(f"  {p.label}: {_level(p)} ({_plain_change(p)})" for p in pts)
        text.append("")

    # --- Canada ---
    ca = _ordered(snapshot, "ca_rates")
    if ca or narrative.canada_comment:
        parts += [_section_head("Canada: rates"), _comment(narrative.canada_comment),
                  _table(ca)]
        plain_block("Canada: rates", narrative.canada_comment, ca)

    # --- United States ---
    us = _ordered(snapshot, "us_rates")
    credit = _ordered(snapshot, "credit")
    if us or credit or narrative.us_comment:
        parts += [_section_head("United States: rates and credit"),
                  _comment(narrative.us_comment), _table(us), _table(credit)]
        if credit:
            parts.append(_note_line(
                "No free Canadian IG or HY option-adjusted spread series exists, "
                "so the US indices above stand in as the read-across for CAD credit."))
        plain_block("United States: rates and credit", narrative.us_comment, us + credit)

        supply = snapshot.auction_results
        if supply:
            lines = [_auction_line(a) for a in supply[:4]]
            parts.append(
                f'<tr><td style="{_PAD}padding-top:12px;">'
                f'<div style="{SANS}font-size:12px;line-height:18px;color:{INK_SOFT};">'
                f'<span style="color:{INK};font-weight:bold;">Supply this week.</span> '
                + "; ".join(html.escape(x) for x in lines) + "</div></td></tr>")
            text += ["Supply this week: " + "; ".join(lines), ""]

    # --- News ---
    if news.items or not news.available or narrative.news_intro:
        parts.append(_section_head("News"))
        text.append("NEWS")
        if narrative.news_intro:
            parts.append(_comment(narrative.news_intro))
            text.append(narrative.news_intro)
        if not news.available:
            parts.append(_comment("News feed unavailable this run."))
            text.append("News feed unavailable this run.")
        for item in news.items:
            parts.append(
                f'<tr><td style="{_PAD}padding-top:12px;">'
                f'<div style="{SANS}font-size:13px;line-height:20px;color:{INK};'
                f'font-weight:bold;">{html.escape(item.headline)}</div>'
                f'<div style="{_BODY_TXT}padding-top:3px;">{html.escape(item.detail)} '
                f'<a href="{html.escape(item.url)}" style="color:{ACCENT};'
                f'text-decoration:none;font-size:11px;">'
                f'{html.escape(item.source_name)}</a></div></td></tr>')
            text.append(f"  - {item.headline}. {item.detail} "
                        f"[{item.source_name}] {item.url}")
        text.append("")

    # --- FX and commodities ---
    fx = _ordered(snapshot, "fx")
    comm = _ordered(snapshot, "commodities")
    if fx or comm or narrative.fx_commodities_comment:
        parts += [_section_head("FX and commodities"),
                  _comment(narrative.fx_commodities_comment), _table(fx), _table(comm)]
        plain_block("FX and commodities", narrative.fx_commodities_comment, fx + comm)
        btc = snapshot.point("btc")
        if btc is not None:
            line = f"Bitcoin {_level(btc)}, {_week(btc)} on the week."
            parts.append(_note_line(line))
            text += [line, ""]

    # --- Equities ---
    eq = _ordered(snapshot, "equities")
    if eq or narrative.equities_comment:
        parts += [_section_head("Equities"), _comment(narrative.equities_comment),
                  _table(eq)]
        plain_block("Equities", narrative.equities_comment, eq)

    # --- What to watch ---
    deck: list[str] = []
    for c in news.calendar:
        line = f"{c.time_et} ET, {c.item}" if c.time_et else c.item
        if c.consensus:
            line += f" (consensus {c.consensus})"
        deck.append(line)
    for c in news.cb_speakers:
        deck.append(f"{c.time_et} ET, {c.item}" if c.time_et else c.item)
    for a in snapshot.auctions_today:
        deck.append(f"Auction: {_auction_line(a)}")
    vol = _ordered(snapshot, "volatility")
    if deck or narrative.watch_comment or vol:
        parts += [_section_head("What to watch"), _comment(narrative.watch_comment)]
        text.append("WHAT TO WATCH")
        if narrative.watch_comment:
            text.append(narrative.watch_comment)
        if deck:
            parts.append(
                f'<tr><td style="{_PAD}padding-top:12px;">'
                f'<div style="{_BODY_TXT}">'
                + "<br>".join(html.escape(d) for d in deck) + "</div></td></tr>")
            text += [f"  - {d}" for d in deck]
        if vol:
            parts.append(_table(vol))
            text += [f"  {p.label}: {_level(p)} ({_plain_change(p)})" for p in vol]
        text.append("")

    # --- Footer ---
    contact = os.environ.get("EMAIL_REPLY_TO") or os.environ.get("OWNER_EMAIL", "")
    footer = (
        f"{cfg.email.title}. {cfg.email.sender_name}. {cfg.email.sender_descriptor} "
        "Weekly moves are close to close against the prior Friday. Sources: FRED, "
        "Bank of Canada, TreasuryDirect, Yahoo Finance. Credit spreads publish with "
        "a lag and are the latest available print. "
        f"To be removed from this list, reply “unsubscribe” to {contact}.")
    parts.append(
        f'<tr><td style="{_PAD}padding-top:26px;padding-bottom:28px;">'
        f'<div style="height:1px;background-color:{RULE};font-size:0;line-height:0;'
        'margin-bottom:14px;">&nbsp;</div>'
        f'<div style="{SANS}font-size:10px;line-height:15px;color:{MUTED};">'
        f'{html.escape(footer)}</div></td></tr>')
    text += ["--", footer]

    html_doc = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(subject)}</title></head>"
        f'<body style="margin:0;padding:0;background-color:{PAGE};">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="background-color:{PAGE};"><tr>'
        '<td align="center" style="padding:20px 10px;">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        'border="0" style="width:600px;max-width:100%;background-color:#FFFFFF;">'
        + "".join(p for p in parts if p) +
        "</table></td></tr></table></body></html>")

    return subject, html_doc, "\n".join(text)
