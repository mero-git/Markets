"""Degraded-data decision path — the fail-closed gate."""

from __future__ import annotations

from datetime import datetime, timezone

from marketbrief.config import HealthConfig
from marketbrief.health import assess_health
from marketbrief.models import DataPoint, FetchFailure, Freshness, Rejection

NOW = datetime(2026, 9, 14, 10, 30, tzinfo=timezone.utc)
HCFG = HealthConfig(critical_keys=("ust_10y", "spx_fut", "ig_oas"), max_failure_ratio=0.25)


def point(key: str) -> DataPoint:
    return DataPoint(key=key, label=key, section="x", value=1.0, unit="index",
                     source="yahoo:x", fetched_at=NOW, asof=NOW, asof_date=None,
                     freshness=Freshness.LIVE)


def failure(key: str) -> FetchFailure:
    return FetchFailure(key=key, source="yahoo:x", reason="boom", fetched_at=NOW)


def rejection(key: str) -> Rejection:
    return Rejection(key=key, source="yahoo:x", reason="implausible", value=1.0,
                     prev_value=None, fetched_at=NOW)


def test_healthy_run():
    pts = [point(k) for k in ("ust_10y", "spx_fut", "ig_oas", "vix")]
    rep = assess_health(attempted=4, points=pts, failures=[], rejections=[], cfg=HCFG)
    assert not rep.degraded and rep.failure_ratio == 0.0


def test_missing_critical_field_degrades():
    pts = [point(k) for k in ("spx_fut", "ig_oas", "vix")]
    rep = assess_health(attempted=4, points=pts, failures=[failure("ust_10y")],
                        rejections=[], cfg=HCFG)
    assert rep.degraded and "ust_10y" in rep.critical_missing


def test_rejected_critical_field_degrades():
    # A critical value rejected by sanity checks counts as missing, not present.
    pts = [point(k) for k in ("ust_10y", "spx_fut")]
    rep = assess_health(attempted=3, points=pts, failures=[],
                        rejections=[rejection("ig_oas")], cfg=HCFG)
    assert rep.degraded and "ig_oas" in rep.critical_missing


def test_noncritical_failures_within_ratio_stay_healthy():
    pts = [point(k) for k in ("ust_10y", "spx_fut", "ig_oas", "a", "b", "c", "d")]
    rep = assess_health(attempted=8, points=pts, failures=[failure("btc")],
                        rejections=[], cfg=HCFG)
    assert not rep.degraded  # 1/8 = 12.5% < 25%


def test_failure_ratio_breach_degrades():
    pts = [point(k) for k in ("ust_10y", "spx_fut", "ig_oas")]
    fails = [failure(k) for k in ("a", "b", "c")]
    rep = assess_health(attempted=6, points=pts, failures=fails, rejections=[], cfg=HCFG)
    assert rep.degraded and any("failure ratio" in r for r in rep.reasons)


def test_no_fetches_at_all_degrades():
    rep = assess_health(attempted=0, points=[], failures=[], rejections=[], cfg=HCFG)
    assert rep.degraded
