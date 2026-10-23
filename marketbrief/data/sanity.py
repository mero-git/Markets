"""Sanity and staleness checks.

A value that fails here is treated as bad data: logged, recorded as a
:class:`Rejection`, and omitted from the snapshot. It is never clamped,
estimated, or passed through.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..config import Config, CorrelatedPair, InstrumentSpec, SanityRule
from .base import RawQuote

log = logging.getLogger(__name__)


@dataclass(slots=True)
class CheckResult:
    ok: bool
    reason: str | None = None


def check_value(quote: RawQuote, rule: SanityRule) -> CheckResult:
    """Level bounds plus plausibility of the one-day move."""
    if not isinstance(quote.value, (int, float)) or quote.value != quote.value:  # NaN guard
        return CheckResult(False, "value is not a number")
    if not (rule.min <= quote.value <= rule.max):
        return CheckResult(False, f"level {quote.value} outside [{rule.min}, {rule.max}]")

    if quote.prev_value is not None:
        if quote.prev_value != quote.prev_value:
            return CheckResult(False, "prev_value is NaN")
        if not (rule.min <= quote.prev_value <= rule.max):
            return CheckResult(False, f"prev level {quote.prev_value} outside [{rule.min}, {rule.max}]")
        if rule.max_change_bp is not None:
            move_bp = abs(quote.value - quote.prev_value) * rule.bp_per_unit
            if move_bp > rule.max_change_bp:
                return CheckResult(False, f"move {move_bp:.1f}bp exceeds {rule.max_change_bp}bp")
        if rule.max_change_pct is not None and quote.prev_value != 0:
            move_pct = abs(quote.value / quote.prev_value - 1.0) * 100.0
            if move_pct > rule.max_change_pct:
                return CheckResult(False, f"move {move_pct:.1f}% exceeds {rule.max_change_pct}%")

    if quote.week_value is not None:
        if quote.week_value != quote.week_value:
            return CheckResult(False, "week_value is NaN")
        if not (rule.min <= quote.week_value <= rule.max):
            return CheckResult(
                False, f"week-ago level {quote.week_value} outside [{rule.min}, {rule.max}]")
        if rule.max_week_change_bp is not None:
            move_bp = abs(quote.value - quote.week_value) * rule.bp_per_unit
            if move_bp > rule.max_week_change_bp:
                return CheckResult(
                    False, f"weekly move {move_bp:.1f}bp exceeds {rule.max_week_change_bp}bp")
        if rule.max_week_change_pct is not None and quote.week_value != 0:
            move_pct = abs(quote.value / quote.week_value - 1.0) * 100.0
            if move_pct > rule.max_week_change_pct:
                return CheckResult(
                    False, f"weekly move {move_pct:.1f}% exceeds {rule.max_week_change_pct}%")
    return CheckResult(True)


def check_correlated_pair(pair: CorrelatedPair, a_pct: float | None,
                          b_pct: float | None) -> CheckResult:
    """Two legs that normally track each other must not diverge implausibly.

    Each leg can pass its own bounds while the pair is plainly wrong, which is
    what a futures contract roll looks like: one leg keeps a stale prior close
    and prints a move its twin did not make. Rejecting both is the safe call,
    because there is no way to tell from the data which leg is the bad one.
    """
    if a_pct is None or b_pct is None:
        return CheckResult(True)
    divergence = abs(a_pct - b_pct)
    if divergence > pair.max_divergence_pct:
        return CheckResult(
            False,
            f"{pair.a} and {pair.b} diverged {divergence:.1f}pp over the "
            f"{pair.basis} (limit {pair.max_divergence_pct}pp); likely a bad "
            "print or a contract roll")
    return CheckResult(True)


def check_staleness(
    quote: RawQuote,
    spec: InstrumentSpec,
    cfg: Config,
    now: datetime,
) -> CheckResult:
    """A 'live' quote or daily observation older than its threshold is failed."""
    limit_hours = spec.staleness_hours or cfg.staleness_hours[spec.freshness]
    limit = timedelta(hours=limit_hours)

    if quote.asof is not None:
        age = now - quote.asof
        if age > limit:
            return CheckResult(False, f"stale: last print {age.total_seconds() / 3600:.1f}h old (limit {limit_hours:.0f}h)")
        return CheckResult(True)

    if quote.asof_date is not None:
        obs = datetime.fromisoformat(quote.asof_date).replace(tzinfo=now.tzinfo)
        age = now - obs
        if age > limit:
            return CheckResult(False, f"stale: observation {quote.asof_date} is {age.total_seconds() / 3600:.0f}h old (limit {limit_hours:.0f}h)")
        return CheckResult(True)

    return CheckResult(False, "no timestamp on quote — cannot assess staleness")


def validate(quote: RawQuote, spec: InstrumentSpec, cfg: Config, now: datetime) -> CheckResult:
    rule = cfg.sanity.get(spec.cls)
    if rule is None:
        return CheckResult(False, f"no sanity rule for class {spec.cls!r}")
    staleness = check_staleness(quote, spec, cfg, now)
    if not staleness.ok:
        return staleness
    value_check = check_value(quote, rule)
    if not value_check.ok:
        log.warning("sanity rejection %s: %s", spec.key, value_check.reason)
    return value_check
