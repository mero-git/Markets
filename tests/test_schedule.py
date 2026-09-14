"""DST correctness and holiday handling — the 6:30 ET invariant."""

from __future__ import annotations

from datetime import date, datetime, timezone

from marketbrief.schedule import (
    delivery_time_utc,
    is_send_day,
    next_send_datetimes_utc,
    should_run,
)


class TestDST:
    def test_winter_send_is_1130_utc(self, cfg):
        # Early March 2026, before DST starts (Mar 8): 06:30 EST == 11:30 UTC.
        assert delivery_time_utc(cfg, date(2026, 3, 6)).hour == 11
        assert delivery_time_utc(cfg, date(2026, 3, 6)).minute == 30

    def test_summer_send_is_1030_utc(self, cfg):
        # After the March transition: 06:30 EDT == 10:30 UTC.
        assert delivery_time_utc(cfg, date(2026, 3, 9)).hour == 10

    def test_november_fallback(self, cfg):
        # DST ends Sun Nov 1 2026: Friday before is EDT, Monday after is EST.
        assert delivery_time_utc(cfg, date(2026, 10, 30)).hour == 10
        assert delivery_time_utc(cfg, date(2026, 11, 2)).hour == 11

    def test_no_drift_across_the_year(self, cfg):
        # The local wall-clock time is 06:30 ET on every send day of 2026.
        from zoneinfo import ZoneInfo
        et = ZoneInfo(cfg.timezone)
        for d in (date(2026, 1, 15), date(2026, 4, 15), date(2026, 7, 15), date(2026, 12, 15)):
            local = delivery_time_utc(cfg, d).astimezone(et)
            assert (local.hour, local.minute) == (6, 30)


class TestHolidays:
    def test_thanksgiving_2026_skipped(self, cfg):
        assert not is_send_day(cfg, date(2026, 11, 26))
        assert is_send_day(cfg, date(2026, 11, 27))

    def test_weekend_skipped(self, cfg):
        assert not is_send_day(cfg, date(2026, 9, 12))  # Saturday
        assert not is_send_day(cfg, date(2026, 9, 13))  # Sunday

    def test_regular_weekday_sends(self, cfg):
        assert is_send_day(cfg, date(2026, 9, 14))  # Monday

    def test_christmas_2026_observed(self, cfg):
        assert not is_send_day(cfg, date(2026, 12, 25))

    def test_next_sends_skip_weekend(self, cfg):
        sends = next_send_datetimes_utc(cfg, date(2026, 9, 11), n=2)
        assert sends[0].date() == date(2026, 9, 11)  # Friday
        assert sends[1].date() == date(2026, 9, 14)  # Monday, not Sat/Sun


class TestRunWindow:
    def test_in_window_runs(self, cfg):
        # 09:00 UTC on an EDT Monday = 05:00 ET, 90min before send.
        now = datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc)
        ok, why = should_run(cfg, now)
        assert ok, why

    def test_too_early_exits(self, cfg):
        now = datetime(2026, 9, 14, 6, 0, tzinfo=timezone.utc)  # 02:00 ET
        ok, why = should_run(cfg, now)
        assert not ok and "too early" in why

    def test_too_late_skips_rather_than_sending(self, cfg):
        # 11:15 UTC EDT = 07:15 ET — 45min past send; a late note is worse than none.
        now = datetime(2026, 9, 14, 11, 15, tzinfo=timezone.utc)
        ok, why = should_run(cfg, now)
        assert not ok and "too late" in why

    def test_holiday_exits(self, cfg):
        now = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)  # Thanksgiving
        ok, why = should_run(cfg, now)
        assert not ok and "not a trading day" in why

    def test_dst_shift_keeps_window_honest(self, cfg):
        # 10:00 UTC is in-window during EDT (06:00 ET) but 05:00 ET during EST —
        # still in the 3h early window, so both crons can fire and only the
        # sleep-to-target differs. 13:00 UTC EST (08:00 ET) must not run.
        assert should_run(cfg, datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc))[0]
        assert should_run(cfg, datetime(2026, 1, 14, 10, 0, tzinfo=timezone.utc))[0]
        assert not should_run(cfg, datetime(2026, 1, 14, 13, 0, tzinfo=timezone.utc))[0]
