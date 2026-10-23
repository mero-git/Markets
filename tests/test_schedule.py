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
    def test_friday_sends(self, cfg):
        assert is_send_day(cfg, date(2026, 10, 23))

    def test_other_weekdays_do_not_send(self, cfg):
        # The note is weekly. Thursday and Monday are not send days.
        assert not is_send_day(cfg, date(2026, 10, 22))
        assert not is_send_day(cfg, date(2026, 10, 26))

    def test_weekend_skipped(self, cfg):
        assert not is_send_day(cfg, date(2026, 10, 24))
        assert not is_send_day(cfg, date(2026, 10, 25))

    def test_good_friday_skipped(self, cfg):
        # The market holiday that most often lands on a send day.
        assert not is_send_day(cfg, date(2026, 4, 3))

    def test_christmas_on_a_friday_skipped(self, cfg):
        assert not is_send_day(cfg, date(2026, 12, 25))

    def test_day_after_thanksgiving_sends(self, cfg):
        # Short session, but the market is open, so the note goes.
        assert is_send_day(cfg, date(2026, 11, 27))

    def test_next_sends_are_a_week_apart(self, cfg):
        sends = next_send_datetimes_utc(cfg, date(2026, 10, 23), n=3)
        assert [d.date() for d in sends] == [
            date(2026, 10, 23), date(2026, 10, 30), date(2026, 11, 6)]


class TestRunWindow:
    def test_in_window_runs(self, cfg):
        # 09:45 UTC on an EDT Friday is 05:45 ET, 45 minutes before the send.
        ok, why = should_run(cfg, datetime(2026, 10, 23, 9, 45, tzinfo=timezone.utc))
        assert ok, why

    def test_too_early_exits(self, cfg):
        ok, why = should_run(cfg, datetime(2026, 10, 23, 8, 30, tzinfo=timezone.utc))
        assert not ok and "too early" in why

    def test_too_late_skips_rather_than_sending(self, cfg):
        # 11:15 UTC EDT is 07:15 ET, 45 minutes past. Late is worse than absent.
        ok, why = should_run(cfg, datetime(2026, 10, 23, 11, 15, tzinfo=timezone.utc))
        assert not ok and "too late" in why

    def test_non_send_weekday_exits(self, cfg):
        ok, why = should_run(cfg, datetime(2026, 10, 22, 9, 45, tzinfo=timezone.utc))
        assert not ok and "not a trading day" in why

    def test_good_friday_exits(self, cfg):
        ok, why = should_run(cfg, datetime(2026, 4, 3, 9, 45, tzinfo=timezone.utc))
        assert not ok and "not a trading day" in why

    def test_exactly_one_cron_qualifies_each_season(self, cfg):
        # Crons fire 09:45 and 10:45 UTC. Summer (EDT, send 10:30 UTC): 09:45
        # is 45min early and runs. Winter (EST, send 11:30 UTC): 09:45 is
        # 105min early and exits, 10:45 is 45min early and runs.
        assert should_run(cfg, datetime(2026, 7, 24, 9, 45, tzinfo=timezone.utc))[0]
        assert not should_run(cfg, datetime(2026, 1, 23, 9, 45, tzinfo=timezone.utc))[0]
        assert should_run(cfg, datetime(2026, 1, 23, 10, 45, tzinfo=timezone.utc))[0]
        assert not should_run(cfg, datetime(2026, 1, 23, 13, 0, tzinfo=timezone.utc))[0]
