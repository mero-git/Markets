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
    """17:15 ET must stay 17:15 ET on both sides of every transition."""

    def test_winter_send_is_2215_utc(self, cfg):
        # Early March 2026, before DST starts on the 8th: 17:15 EST = 22:15 UTC.
        t = delivery_time_utc(cfg, date(2026, 3, 6))
        assert (t.hour, t.minute) == (22, 15)

    def test_summer_send_is_2115_utc(self, cfg):
        # After the March transition: 17:15 EDT = 21:15 UTC.
        assert delivery_time_utc(cfg, date(2026, 3, 13)).hour == 21

    def test_november_fallback(self, cfg):
        # DST ends Sun Nov 1 2026. The Friday before is EDT, the one after EST.
        assert delivery_time_utc(cfg, date(2026, 10, 30)).hour == 21
        assert delivery_time_utc(cfg, date(2026, 11, 6)).hour == 22

    def test_no_drift_across_the_year(self, cfg):
        from zoneinfo import ZoneInfo
        et = ZoneInfo(cfg.timezone)
        for d in (date(2026, 1, 16), date(2026, 4, 17), date(2026, 7, 17),
                  date(2026, 12, 18)):
            local = delivery_time_utc(cfg, d).astimezone(et)
            assert (local.hour, local.minute) == (17, 15)


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
        # 20:30 UTC on an EDT Friday is 16:30 ET, 45 minutes before the send.
        ok, why = should_run(cfg, datetime(2026, 10, 23, 20, 30, tzinfo=timezone.utc))
        assert ok, why

    def test_too_early_exits(self, cfg):
        ok, why = should_run(cfg, datetime(2026, 10, 23, 19, 30, tzinfo=timezone.utc))
        assert not ok and "too early" in why

    def test_too_late_skips_rather_than_sending(self, cfg):
        # 75 minutes past the target. A recap that lands in the evening is
        # worse than one that does not land at all.
        ok, why = should_run(cfg, datetime(2026, 10, 23, 22, 30, tzinfo=timezone.utc))
        assert not ok and "too late" in why

    def test_modest_delay_still_sends(self, cfg):
        # Actions cron routinely fires late. 15 minutes past is still fine.
        assert should_run(cfg, datetime(2026, 10, 23, 21, 30, tzinfo=timezone.utc))[0]

    def test_non_send_weekday_exits(self, cfg):
        ok, why = should_run(cfg, datetime(2026, 10, 22, 20, 30, tzinfo=timezone.utc))
        assert not ok and "not a trading day" in why

    def test_good_friday_exits(self, cfg):
        ok, why = should_run(cfg, datetime(2026, 4, 3, 20, 30, tzinfo=timezone.utc))
        assert not ok and "not a trading day" in why

    def test_exactly_one_cron_qualifies_each_season(self, cfg):
        # Crons fire 20:30 and 21:30 UTC. Summer (EDT, send 21:15 UTC): 20:30
        # is 45min early and runs. Winter (EST, send 22:15 UTC): 20:30 is
        # 105min early and exits, 21:30 is 45min early and runs.
        assert should_run(cfg, datetime(2026, 7, 24, 20, 30, tzinfo=timezone.utc))[0]
        assert not should_run(cfg, datetime(2026, 1, 23, 20, 30, tzinfo=timezone.utc))[0]
        assert should_run(cfg, datetime(2026, 1, 23, 21, 30, tzinfo=timezone.utc))[0]
        assert not should_run(cfg, datetime(2026, 1, 23, 23, 45, tzinfo=timezone.utc))[0]
