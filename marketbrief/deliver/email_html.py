"""Outlook-safe email rendering: table-based layout, fully inline CSS, no
flexbox/grid/media-query dependence, plus a plain-text alternative part.

All numeric tables are rendered here, deterministically, from the snapshot
— the model never touches them. Sections with no data and no commentary are
omitted entirely; empty headers are never printed.
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

# Visual constants (inline everywhere — Outlook strips <style> unreliably)
_BODY = "margin:0;padding:0;background-color:#f4f4f2;"
_WRAP = ("width:600px;max-width:100%;background-color:#ffffff;"
         "font-family:Georgia,'Times New Roman',serif;color:#1a1a1a;")
_SANS = "font-family:Arial,Helvetica,sans-serif;"
_H2 = (_SANS + "font-size:12px;letter-spacing:2px;text-transform:uppercase;"
       "color:#8a7040;margin:0;padding:18px 24px 6px 24px;font-weight:bold;")
_TD = _SANS + "font-size:13px;color:#1a1a1a;padding:3px 0;"
_MUTED = _SANS + "font-size:10px;color:#999999;"
_COMMENT = "font-size:13px;line-height:19px;color:#333333;padding:4px 24px 2px 24px;"

_CURVE_ORDER = ["ust_2y", "ust_5y", "ust_10y", "ust_30y", "ust_2s10s", "ust_5s30s",
                "ust_2y_fut", "ust_5y_fut", "ust_10y_fut", "ust_30y_fut",
                "ust_2s10s_fut", "ust_5s30s_fut",
                "tips_10y_real", "breakeven_10y", "bund_10y", "jgb_10y", "gilt_10y"]
_CANADA_ORDER = ["goc_2y", "goc_10y", "ca_us_10y", "tsx"]


def _fmt_level(p: DataPoint) -> str:
    if p.unit == "%":
        return f"{p.value:.2f}%"
    if p.unit == "bp":
        return f"{p.value:.0f}bp"
    if p.unit == "rate":
        return f"{p.value:.4f}" if p.value < 20 else f"{p.value:.2f}"
    if abs(p.value) >= 1000:
        return f"{p.value:,.0f}"
    return f"{p.value:,.2f}"


def _fmt_change(p: DataPoint) -> str:
    if p.change is None:
        return "—"
    if p.change_unit == "bp":
        return f"{p.change:+.1f}bp"
    if p.change_pct is not None:
        return f"{p.change_pct:+.2f}%"
    return f"{p.change:+,.2f}"


def _chg_color(p: DataPoint) -> str:
    if p.change is None or p.change == 0:
        return "#666666"
    return "#0a7a3d" if p.change > 0 else "#b42222"


def _asof_tag(p: DataPoint) -> str:
    if p.asof is not None:
        return p.asof.astimezone(_ET).strftime("%H:%M ET")
    if p.asof_date:
        return f"close {p.asof_date[5:]}"
    return ""


def _row(p: DataPoint) -> str:
    return (
        "<tr>"
        f'<td style="{_TD}">{html.escape(p.label)}</td>'
        f'<td style="{_TD}text-align:right;font-weight:bold;">{_fmt_level(p)}</td>'
        f'<td style="{_TD}text-align:right;color:{_chg_color(p)};">{_fmt_change(p)}</td>'
        f'<td style="{_MUTED}text-align:right;padding:3px 0;">{_asof_tag(p)}</td>'
        "</tr>"
    )


def _data_table(points: list[DataPoint]) -> str:
    rows = "".join(_row(p) for p in points)
    return ('<tr><td style="padding:2px 24px 8px 24px;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'border="0">' + rows + "</table></td></tr>")


def _ordered(snapshot: MarketSnapshot, section: str, order: list[str] | None = None
             ) -> list[DataPoint]:
    pts = [p for p in snapshot.points if p.section == section]
    if order:
        rank = {k: i for i, k in enumerate(order)}
        pts.sort(key=lambda p: rank.get(p.key, 99))
    return pts


def _comment(text: str | None) -> str:
    if not text:
        return ""
    return f'<tr><td style="{_SANS}{_COMMENT}">{html.escape(text)}</td></tr>'


def _header(title: str) -> str:
    return f'<tr><td style="{_H2}">{title}</td></tr>'


def _auction_line(a: Auction) -> str:
    bits = [x for x in (a.security_term, a.security_type) if x]
    line = " ".join(bits) or "Security"
    if a.offering_amount:
        line += f" — ${a.offering_amount / 1e9:.0f}bn"
    if a.is_result:
        if a.high_yield is not None:
            line += f", high yield {a.high_yield:.3f}%"
        elif a.high_discount_rate is not None:
            line += f", high rate {a.high_discount_rate:.3f}%"
        if a.bid_to_cover is not None:
            line += f", bid-to-cover {a.bid_to_cover:.2f}"
        if a.auction_date:
            line += f" ({a.auction_date})"
    elif a.typical_close_time_et:
        line += f", closes ~{a.typical_close_time_et} ET (typical)"
    return line


def render_email(cfg: Config, snapshot: MarketSnapshot, narrative: BriefNarrative,
                 news: NewsBundle, *, tier: str) -> tuple[str, str, str]:
    """Return (subject, html, plain_text)."""
    date_et = datetime.fromisoformat(snapshot.trading_date)
    nice_date = date_et.strftime("%a %b %-d, %Y")
    subject = f"{cfg.email.subject_prefix} — {nice_date}"
    if tier == "self" and snapshot.health.degraded:
        subject = "[DEGRADED] " + subject

    parts: list[str] = []
    text: list[str] = [f"{cfg.email.subject_prefix} — {nice_date}", ""]

    # Masthead
    parts.append(
        '<tr><td style="background-color:#14140f;padding:20px 24px;">'
        f'<div style="{_SANS}font-size:16px;font-weight:bold;color:#ffffff;letter-spacing:1px;">'
        f'{html.escape(cfg.email.subject_prefix)}</div>'
        f'<div style="{_SANS}font-size:11px;color:#b8a26a;padding-top:4px;">'
        f'{nice_date} &nbsp;·&nbsp; 6:30am ET</div></td></tr>')

    if snapshot.health.degraded:
        parts.append(
            '<tr><td style="background-color:#8a1c1c;padding:10px 24px;'
            f'{_SANS}font-size:12px;color:#ffffff;">DEGRADED RUN — data quality below '
            "threshold; not distributed beyond self. See run log.</td></tr>")
        text += ["** DEGRADED RUN — not distributed beyond self **", ""]

    if snapshot.sifma_open_today is False:
        parts.append(
            '<tr><td style="background-color:#f4ead8;padding:8px 24px;'
            f'{_SANS}font-size:12px;color:#6b5620;">US bond market closed today (SIFMA).'
            "</td></tr>")
        text += ["US bond market closed today (SIFMA).", ""]

    # TLDR
    tldr_html = "<br>".join(html.escape(line) for line in narrative.tldr)
    parts.append(
        '<tr><td style="background-color:#faf7ef;border-left:4px solid #8a7040;'
        'padding:14px 20px;margin:0;">'
        f'<div style="{_SANS}font-size:14px;line-height:21px;color:#1a1a1a;">{tldr_html}'
        "</div></td></tr>")
    text += [*narrative.tldr, ""]

    # ---- Fixed income ----
    curve = _ordered(snapshot, "curve", _CURVE_ORDER)
    if curve:
        parts += [_header("Fixed Income — Curve"), _comment(narrative.curve_comment),
                  _data_table(curve)]
        text.append("FIXED INCOME — CURVE")
        if narrative.curve_comment:
            text.append(narrative.curve_comment)
        text += [f"  {p.label}: {_fmt_level(p)} ({_fmt_change(p)})" for p in curve] + [""]

    credit = _ordered(snapshot, "credit")
    if credit or narrative.credit_comment:
        parts += [_header("Credit"), _comment(narrative.credit_comment),
                  _data_table(credit) if credit else ""]
        text.append("CREDIT")
        if narrative.credit_comment:
            text.append(narrative.credit_comment)
        text += [f"  {p.label}: {_fmt_level(p)} ({_fmt_change(p)})" for p in credit] + [""]

    auctions = snapshot.auctions_today
    results = snapshot.auction_results
    if auctions or results or narrative.supply_comment:
        parts += [_header("Supply & Primary"), _comment(narrative.supply_comment)]
        lines = []
        if auctions:
            lines.append("<b>Auctions today:</b> " +
                         "; ".join(html.escape(_auction_line(a)) for a in auctions))
            text.append("SUPPLY — auctions today: " +
                        "; ".join(_auction_line(a) for a in auctions))
        else:
            lines.append("No Treasury auctions scheduled today.")
            text.append("SUPPLY — no Treasury auctions scheduled today.")
        if results:
            lines.append("<b>Recent results:</b> " +
                         "; ".join(html.escape(_auction_line(a)) for a in results))
            text.append("Recent results: " + "; ".join(_auction_line(a) for a in results))
        parts.append('<tr><td style="' + _SANS + _COMMENT + '">' +
                     "<br>".join(lines) + "</td></tr>")
        text.append("")

    # ---- Material news ----
    if news.items or news.note or narrative.news_intro:
        parts.append(_header("Material News"))
        text.append("MATERIAL NEWS")
        if narrative.news_intro:
            parts.append(_comment(narrative.news_intro))
            text.append(narrative.news_intro)
        if not news.available:
            parts.append(_comment("News feed unavailable this run."))
            text.append("News feed unavailable this run.")
        for item in news.items:
            parts.append(
                f'<tr><td style="{_SANS}{_COMMENT}">'
                f"<b>{html.escape(item.headline)}</b> — {html.escape(item.detail)} "
                f'<a href="{html.escape(item.url)}" style="color:#8a7040;">'
                f"[{html.escape(item.source_name)}]</a></td></tr>")
            text.append(f"  • {item.headline} — {item.detail} [{item.source_name}] {item.url}")
        text.append("")

    # ---- Supporting markets ----
    for title, sections, order, comment in (
        ("Equities", ["equity_futures", "asia", "europe"], None,
         narrative.equities_comment),
        ("FX & Commodities", ["fx", "commodities", "volatility", "crypto"], None,
         narrative.fx_commodities_comment),
    ):
        pts: list[DataPoint] = []
        for s in sections:
            pts += _ordered(snapshot, s)
        if not pts:
            continue
        parts += [_header(title), _comment(comment), _data_table(pts)]
        text.append(title.upper())
        if comment:
            text.append(comment)
        text += [f"  {p.label}: {_fmt_level(p)} ({_fmt_change(p)})" for p in pts] + [""]

    # ---- Canada corner ----
    canada = _ordered(snapshot, "canada", _CANADA_ORDER)
    usdcad = snapshot.point("usdcad")
    if usdcad is not None:
        canada = canada + [usdcad]
    if canada:
        parts += [_header("Canada Corner"), _comment(narrative.canada_comment),
                  _data_table(canada)]
        text.append("CANADA CORNER")
        if narrative.canada_comment:
            text.append(narrative.canada_comment)
        text += [f"  {p.label}: {_fmt_level(p)} ({_fmt_change(p)})" for p in canada] + [""]

    # ---- On deck ----
    deck: list[str] = []
    for c in news.calendar:
        line = f"{c.time_et} ET — {c.item}" if c.time_et else c.item
        if c.consensus:
            line += f" (cons. {c.consensus})"
        deck.append(line)
    for c in news.cb_speakers:
        deck.append(f"{c.time_et} ET — {c.item}" if c.time_et else c.item)
    for a in auctions:
        deck.append(f"Auction: {_auction_line(a)}")
    if deck:
        parts.append(_header("On Deck Today"))
        parts.append('<tr><td style="' + _SANS + _COMMENT + '">' +
                     "<br>".join(html.escape(d) for d in deck) + "</td></tr>")
        text += ["ON DECK TODAY", *[f"  • {d}" for d in deck], ""]

    # ---- Footer ----
    contact = os.environ.get("EMAIL_REPLY_TO") or os.environ.get("OWNER_EMAIL", "")
    footer = (
        f"{cfg.email.subject_prefix} · {cfg.email.sender_name} · "
        f"{cfg.email.sender_affiliation}. Data: FRED, TreasuryDirect, Bank of Canada, "
        "Bundesbank, Japan MoF, Bank of England, Yahoo Finance; daily official series "
        "are prior-close prints. Nothing here is investment advice. "
        f"To be removed from this list, reply “unsubscribe” to {contact}.")
    parts.append(
        '<tr><td style="border-top:1px solid #e5e0d5;padding:14px 24px 20px 24px;'
        f'{_MUTED}line-height:15px;">{html.escape(footer)}</td></tr>')
    text += ["--", footer]

    html_doc = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width'>"
        f"<title>{html.escape(subject)}</title></head>"
        f"<body style=\"{_BODY}\">"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="background-color:#f4f4f2;"><tr><td align="center" '
        'style="padding:16px 8px;">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        f'border="0" style="{_WRAP}">'
        + "".join(p for p in parts if p) +
        "</table></td></tr></table></body></html>")

    return subject, html_doc, "\n".join(text)
