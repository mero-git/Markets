"""Sanity and staleness checks — the wall between bad data and the note."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from marketbrief.config import InstrumentSpec, SanityRule
from marketbrief.data.base import RawQuote
from marketbrief.data.sanity import check_staleness, check_value, validate

NOW = datetime(2026, 9, 14, 10, 30, tzinfo=timezone.utc)

EQUITY_RULE = SanityRule(min=50, max=200000, max_change_pct=12)
YIELD_RULE = SanityRule(min=-1.5, max=12, max_change_bp=80)
VOL_RULE = SanityRule(min=5, max=200, max_change_pct=200)


def q(value, prev=None, *, asof=NOW, asof_date=None) -> RawQuote:
    return RawQuote(value=value, prev_value=prev, asof=asof, asof_date=asof_date)


def spec(**over) -> InstrumentSpec:
    base = dict(key="spx_fut", label="S&P fut", section="equity_futures",
                source="yahoo", symbol="ES=F", cls="equity", freshness="live",
                unit="index")
    base.update(over)
    return InstrumentSpec(**base)


class TestValueChecks:
    def test_normal_value_passes(self):
        assert check_value(q(6500.0, 6450.0), EQUITY_RULE).ok

    def test_forty_pct_index_move_rejected(self):
        res = check_value(q(9000.0, 6400.0), EQUITY_RULE)
        assert not res.ok and "exceeds" in res.reason

    def test_negative_vix_rejected(self):
        assert not check_value(q(-3.0, 15.0), VOL_RULE).ok

    def test_500bp_overnight_10y_move_rejected(self):
        res = check_value(q(9.10, 4.10), YIELD_RULE)
        assert not res.ok and "bp" in res.reason

    def test_plausible_yield_move_passes(self):
        assert check_value(q(4.32, 4.25), YIELD_RULE).ok

    def test_deeply_negative_yield_rejected(self):
        assert not check_value(q(-4.0, -0.5), YIELD_RULE).ok

    def test_nan_rejected(self):
        assert not check_value(q(float("nan"), 100.0), EQUITY_RULE).ok

    def test_bad_prev_value_rejected_not_passed_through(self):
        # A corrupt previous close must not silently produce a huge "move".
        assert not check_value(q(6500.0, 3.0), EQUITY_RULE).ok

    def test_missing_prev_is_allowed_level_only(self):
        # No previous value: level check only; change stays None downstream.
        assert check_value(q(6500.0, None), EQUITY_RULE).ok


class TestStaleness:
    def test_fresh_live_quote_passes(self, cfg):
        res = check_staleness(q(6500.0, asof=NOW - timedelta(hours=2)), spec(), cfg, NOW)
        assert res.ok

    def test_stale_live_quote_fails(self, cfg):
        res = check_staleness(q(6500.0, asof=NOW - timedelta(hours=30)), spec(), cfg, NOW)
        assert not res.ok and "stale" in res.reason

    def test_daily_obs_within_window_passes(self, cfg):
        s = spec(key="ust_10y", source="fred", symbol="DGS10", cls="yield_pct",
                 freshness="daily", unit="%")
        res = check_staleness(q(4.1, asof=None, asof_date="2026-09-11"), s, cfg, NOW)
        assert res.ok

    def test_daily_obs_too_old_fails(self, cfg):
        s = spec(key="ust_10y", source="fred", symbol="DGS10", cls="yield_pct",
                 freshness="daily", unit="%")
        res = check_staleness(q(4.1, asof=None, asof_date="2026-09-01"), s, cfg, NOW)
        assert not res.ok

    def test_per_instrument_staleness_override(self, cfg):
        s = spec(key="ig_oas", source="fred", symbol="BAMLC0A0CM", cls="spread_bp",
                 freshness="daily", unit="bp", staleness_hours=200)
        res = check_staleness(q(95.0, asof=None, asof_date="2026-09-08"), s, cfg, NOW)
        assert res.ok  # 200h window keeps the lagged credit series alive

    def test_no_timestamp_fails(self, cfg):
        res = check_staleness(q(6500.0, asof=None, asof_date=None), spec(), cfg, NOW)
        assert not res.ok


class TestValidateIntegration:
    def test_stale_beats_value_check(self, cfg):
        res = validate(q(6500.0, 6450.0, asof=NOW - timedelta(days=4)), spec(), cfg, NOW)
        assert not res.ok and "stale" in res.reason

    def test_unknown_class_rejected(self, cfg):
        res = validate(q(1.0), spec(cls="nonexistent"), cfg, NOW)
        assert not res.ok
