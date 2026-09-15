"""Numeric post-validation — no number reaches the note without provenance."""

from __future__ import annotations

from datetime import datetime, timezone

from marketbrief.generate.verify import (
    build_allowed_numbers,
    find_violations,
    verify_narrative,
)
from marketbrief.models import (
    Auction, DataPoint, Freshness, HealthReport, MarketSnapshot,
)
from marketbrief.news.search import CalendarItem, NewsBundle, NewsItem

NOW = datetime(2026, 9, 14, 10, 30, tzinfo=timezone.utc)


def _point(key: str, label: str, section: str, value: float, unit: str,
           change: float | None = None, change_pct: float | None = None,
           prev: float | None = None) -> DataPoint:
    return DataPoint(key=key, label=label, section=section, value=value, unit=unit,
                     source="test", fetched_at=NOW, asof=NOW, asof_date=None,
                     freshness=Freshness.LIVE, prev_value=prev, change=change,
                     change_unit="bp" if unit in ("%", "bp") else unit,
                     change_pct=change_pct)


def _snapshot() -> MarketSnapshot:
    points = [
        _point("ust_10y", "UST 10Y (prior close)", "curve", 4.96, "%", change=1.0),
        _point("ust_2s10s", "2s10s (prior close)", "curve", 33.0, "bp", change=-6.0),
        _point("hy_oas", "US HY OAS", "credit", 265.0, "bp", change=-5.0, prev=270.0),
        _point("spx_fut", "S&P 500 fut", "equity_futures", 7695.75, "index",
               change=36.25, change_pct=0.47),
    ]
    auction = Auction(security_type="Bond", security_term="29-Year 11-Month",
                      cusip=None, auction_date="2026-09-10", offering_amount=22e9,
                      typical_close_time_et="13:00", high_yield=5.308,
                      bid_to_cover=2.61, is_result=True)
    return MarketSnapshot(
        generated_at=NOW, trading_date="2026-09-14", nyse_open_today=True,
        sifma_open_today=True, points=points, auctions_today=[],
        auction_results=[auction], failures=[], rejections=[],
        health=HealthReport(attempted=4, fetched=4, failed=0, rejected=0,
                            critical_missing=[], failure_ratio=0.0, degraded=False),
    )


NEWS = NewsBundle(
    items=[NewsItem(headline="Acme downgraded to BB+", detail="Cut one notch on leverage of 4.2x.",
                    source_name="Reuters", url="https://reuters.com/x123/story-456")],
    calendar=[CalendarItem(time_et="08:30", item="US CPI m/m", consensus="0.3%")],
)


class TestAllowedSet:
    def test_payload_numbers_allowed(self):
        allowed = build_allowed_numbers(_snapshot(), NEWS)
        for tok in ("4.96", "33", "6", "265", "5", "7695.75", "0.47", "36.25"):
            assert tok in allowed, tok

    def test_label_and_term_numbers_allowed(self):
        allowed = build_allowed_numbers(_snapshot(), NEWS)
        # "S&P 500", "2s10s", "29-Year 11-Month", auction time, sizes
        for tok in ("500", "2", "10", "29", "11", "13:00".split(":")[0], "22", "5.308", "2.61"):
            assert tok in allowed, tok

    def test_news_numbers_allowed(self):
        allowed = build_allowed_numbers(_snapshot(), NEWS)
        for tok in ("4.2", "0.3", "8:30".split(":")[0], "30"):
            assert tok in allowed, tok


class TestViolations:
    def test_clean_text_passes(self):
        allowed = build_allowed_numbers(_snapshot(), NEWS)
        text = ("10Y unchanged at 4.96 into supply; 2s10s 6bp flatter at 33bp. "
                "HY 5bp tighter at 265. ES +0.47%. CPI at 8:30 ET, consensus 0.3%.")
        assert find_violations(text, allowed) == []

    def test_invented_number_fails(self):
        allowed = build_allowed_numbers(_snapshot(), NEWS)
        assert find_violations("10Y at 4.87 overnight", allowed) == ["4.87"]

    def test_model_arithmetic_fails(self):
        # 265 - 5 = 260 is not in the payload — computed numbers are banned.
        allowed = build_allowed_numbers(_snapshot(), NEWS)
        assert "260" in find_violations("HY tightened from 270 to 260", allowed)

    def test_sign_and_trailing_zero_tolerated(self):
        allowed = build_allowed_numbers(_snapshot(), NEWS)
        assert find_violations("moved -6.0bp; index 7,695.75", allowed) == []

    def test_url_digits_ignored(self):
        allowed = build_allowed_numbers(_snapshot(), NEWS)
        assert find_violations(
            "Downgrade [Reuters] https://reuters.com/x123/story-456", allowed) == []

    def test_verify_narrative_reports_field(self):
        failures = verify_narrative(
            {"tldr": "10Y at 4.96", "credit_comment": "HY at 999bp"},
            _snapshot(), NEWS)
        assert list(failures) == ["credit_comment"] and failures["credit_comment"] == ["999"]

    def test_billions_shorthand_allowed(self):
        allowed = build_allowed_numbers(_snapshot(), NEWS)
        assert find_violations("$22bn long bond reopening tailed nothing", allowed) == []
