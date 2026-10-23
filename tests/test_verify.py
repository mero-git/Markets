"""Numeric post-validation. No number reaches the note without provenance."""

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

NOW = datetime(2026, 10, 23, 10, 30, tzinfo=timezone.utc)


def _point(key: str, label: str, section: str, value: float, unit: str,
           change=None, change_pct=None, prev=None,
           change_week=None, change_week_pct=None, week_value=None) -> DataPoint:
    return DataPoint(key=key, label=label, section=section, value=value, unit=unit,
                     source="test", fetched_at=NOW, asof=NOW, asof_date=None,
                     freshness=Freshness.DAILY, prev_value=prev, change=change,
                     change_unit="bp" if unit in ("%", "bp") else unit,
                     change_pct=change_pct, week_value=week_value,
                     week_asof_date="2026-10-16", change_week=change_week,
                     change_week_pct=change_week_pct)


def _snapshot() -> MarketSnapshot:
    points = [
        _point("goc_10y", "GoC 10Y", "ca_rates", 3.78, "%", change=-12.0,
               change_week=-21.0, week_value=3.99),
        _point("ca_us_10y", "Canada-US 10Y", "ca_rates", -118.0, "bp",
               change=5.0, change_week=9.0),
        _point("ust_10y", "UST 10Y", "us_rates", 4.96, "%", change=-17.0,
               change_week=-30.0, week_value=5.26),
        _point("hy_oas", "US HY OAS", "credit", 340.0, "bp", change=13.0,
               prev=327.0, change_week=24.0, week_value=316.0),
        _point("tsx", "S&P/TSX Composite", "equities", 35788.18, "index",
               change=-185.32, change_pct=-0.52, change_week=402.5,
               change_week_pct=1.14, week_value=35385.68),
        # Index names carry digits the model will legitimately reuse.
        _point("spx", "S&P 500", "equities", 6702.11, "index",
               change_pct=-0.18, change_week_pct=0.62),
    ]
    auction = Auction(security_type="Bond", security_term="29-Year 11-Month",
                      cusip=None, auction_date="2026-10-20", offering_amount=22e9,
                      typical_close_time_et="13:00", high_yield=5.308,
                      bid_to_cover=2.61, is_result=True)
    return MarketSnapshot(
        generated_at=NOW, trading_date="2026-10-23", nyse_open_today=True,
        sifma_open_today=True, points=points, auctions_today=[],
        auction_results=[auction], failures=[], rejections=[],
        health=HealthReport(attempted=5, fetched=5, failed=0, rejected=0,
                            critical_missing=[], failure_ratio=0.0, degraded=False),
    )


NEWS = NewsBundle(
    items=[NewsItem(headline="Acme downgraded to BB+",
                    detail="Cut one notch on leverage of 4.2x.",
                    source_name="Reuters", url="https://reuters.com/x123/story-456")],
    calendar=[CalendarItem(time_et="08:30", item="Canada CPI", consensus="0.3%")],
)


class TestAllowedSet:
    def test_weekly_figures_allowed(self):
        allowed = build_allowed_numbers(_snapshot(), NEWS)
        for tok in ("21", "30", "24", "9", "1.14", "402.5", "3.99", "5.26"):
            assert tok in allowed, tok

    def test_daily_figures_still_allowed(self):
        allowed = build_allowed_numbers(_snapshot(), NEWS)
        for tok in ("12", "17", "13", "0.52", "185.32"):
            assert tok in allowed, tok

    def test_label_and_auction_numbers_allowed(self):
        allowed = build_allowed_numbers(_snapshot(), NEWS)
        for tok in ("10", "500", "29", "11", "22", "5.308", "2.61"):
            assert tok in allowed, tok


class TestViolations:
    def test_clean_weekly_text_passes(self):
        allowed = build_allowed_numbers(_snapshot(), NEWS)
        text = ("GoC 10Y fell 21bp on the week to 3.78%, against 30bp in the US "
                "10Y. Canada-US 10Y at -118bp, 9bp wider. HY OAS 24bp wider at "
                "340bp. TSX +1.14%.")
        assert find_violations(text, allowed) == []

    def test_invented_number_fails(self):
        allowed = build_allowed_numbers(_snapshot(), NEWS)
        assert find_violations("GoC 10Y at 3.71%", allowed) == ["3.71"]

    def test_model_arithmetic_fails(self):
        # 340 - 24 = 316 is in the payload as week_value, but 341 is not.
        allowed = build_allowed_numbers(_snapshot(), NEWS)
        assert "341" in find_violations("HY widened to 341bp", allowed)

    def test_stale_figure_from_an_earlier_fetch_fails(self):
        # The real failure this caught in practice: quoting a prior run's print.
        allowed = build_allowed_numbers(_snapshot(), NEWS)
        assert find_violations("TSX -0.45% on the day", allowed) == ["0.45"]

    def test_url_digits_ignored(self):
        allowed = build_allowed_numbers(_snapshot(), NEWS)
        assert find_violations(
            "Downgrade, Reuters https://reuters.com/x123/story-456", allowed) == []

    def test_verify_narrative_reports_offending_field(self):
        failures = verify_narrative(
            {"three_points": "GoC 10Y -21bp", "us_comment": "HY at 999bp"},
            _snapshot(), NEWS)
        assert list(failures) == ["us_comment"]
        assert failures["us_comment"] == ["999"]
