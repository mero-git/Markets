"""Human-readable verification table for a snapshot.

This is the hand-check view: every value with its change, as-of time (ET),
and source, plus failures, rejections, and the health verdict — so the
numbers can be verified against a terminal before any model touches them.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .models import Auction, DataPoint, MarketSnapshot

_ET = ZoneInfo("America/New_York")

_SECTION_ORDER = [
    "ca_rates", "us_rates", "credit", "fx", "commodities", "equities",
    "volatility", "crypto",
]


def _fmt_value(p: DataPoint) -> str:
    if p.unit == "%":
        return f"{p.value:.3f}%"
    if p.unit == "bp":
        return f"{p.value:.1f}bp"
    if abs(p.value) >= 1000:
        return f"{p.value:,.2f}"
    return f"{p.value:.4g}"


def _fmt_change(p: DataPoint) -> str:
    return _fmt_delta(p.change, p.change_pct, p.change_unit)


def _fmt_week(p: DataPoint) -> str:
    return _fmt_delta(p.change_week, p.change_week_pct, p.change_unit)


def _fmt_delta(value, pct, unit) -> str:
    if value is None and pct is None:
        return "n/a"
    if unit == "bp" and value is not None:
        return f"{value:+.1f}bp"
    if pct is not None:
        return f"{pct:+.2f}%"
    return f"{value:+,.4g}"


def _fmt_asof(p: DataPoint) -> str:
    if p.asof is not None:
        return p.asof.astimezone(_ET).strftime("%m-%d %H:%M ET")
    if p.asof_date is not None:
        return f"{p.asof_date} (obs date)"
    return "?"


def _fmt_auction(a: Auction) -> str:
    bits = [x for x in (a.security_term, a.security_type) if x]
    desc = " ".join(bits) or "unknown security"
    if a.offering_amount:
        desc += f"  ${a.offering_amount / 1e9:.0f}bn"
    if a.auction_date:
        desc += f"  {a.auction_date}"
    if not a.is_result and a.typical_close_time_et:
        desc += f"  closes ~{a.typical_close_time_et} ET (typical)"
    if a.is_result:
        if a.high_yield is not None:
            desc += f"  high yield {a.high_yield:.3f}%"
        elif a.high_discount_rate is not None:
            desc += f"  high rate {a.high_discount_rate:.3f}%"
        if a.bid_to_cover is not None:
            desc += f"  b/c {a.bid_to_cover:.2f}"
    return desc


def render_table(snap: MarketSnapshot) -> str:
    lines: list[str] = []
    gen_et = snap.generated_at.astimezone(_ET)
    lines.append(f"MARKET SNAPSHOT — trading date {snap.trading_date}, "
                 f"generated {gen_et.strftime('%Y-%m-%d %H:%M:%S ET')}")
    lines.append(f"NYSE open today: {snap.nyse_open_today}   "
                 f"SIFMA (bond mkt) open today: {snap.sifma_open_today}")
    lines.append("")

    sections: dict[str, list[DataPoint]] = {}
    for p in snap.points:
        sections.setdefault(p.section, []).append(p)

    order = _SECTION_ORDER + [s for s in sections if s not in _SECTION_ORDER]
    header = (f"{'instrument':<28}{'value':>12}{'1 week':>12}{'1 day':>12}  "
              f"{'as of':<20}{'week ago':<12}{'source':<26}note")
    for section in order:
        pts = sections.get(section)
        if not pts:
            continue
        lines.append(f"── {section.upper().replace('_', ' ')} " + "─" * max(0, 100 - len(section)))
        lines.append(header)
        for p in pts:
            lines.append(
                f"{p.label:<28}{_fmt_value(p):>12}{_fmt_week(p):>12}"
                f"{_fmt_change(p):>12}  {_fmt_asof(p):<20}"
                f"{p.week_asof_date or '-':<12}{p.source:<26}{p.note or ''}"
            )
        lines.append("")

    lines.append("── TREASURY SUPPLY " + "─" * 85)
    if snap.auctions_today:
        lines.append("Auctions today:")
        lines.extend(f"  • {_fmt_auction(a)}" for a in snap.auctions_today)
    else:
        lines.append("Auctions today: none in TreasuryDirect feed")
    if snap.auction_results:
        lines.append("Recent results:")
        lines.extend(f"  • {_fmt_auction(a)}" for a in snap.auction_results)
    else:
        lines.append("Recent results: none in window")
    lines.append("")

    if snap.failures:
        lines.append("── FAILURES (omitted from output) " + "─" * 70)
        lines.extend(f"  ✗ {f.key} [{f.source}]: {f.reason}" for f in snap.failures)
        lines.append("")
    if snap.rejections:
        lines.append("── SANITY/STALENESS REJECTIONS (omitted from output) " + "─" * 50)
        lines.extend(
            f"  ✗ {r.key} [{r.source}]: {r.reason} (value={r.value}, prev={r.prev_value})"
            for r in snap.rejections
        )
        lines.append("")

    h = snap.health
    verdict = "DEGRADED — would NOT send beyond self" if h.degraded else "HEALTHY"
    lines.append(f"── HEALTH: {verdict}")
    lines.append(f"   attempted={h.attempted} fetched={h.fetched} failed={h.failed} "
                 f"rejected={h.rejected} failure_ratio={h.failure_ratio:.0%}")
    for reason in h.reasons:
        lines.append(f"   reason: {reason}")
    return "\n".join(lines)
