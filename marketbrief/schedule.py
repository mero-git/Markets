"""Scheduling: delivery-time math (DST-correct) and market-holiday logic.

The send time is a local America/New_York wall-clock time resolved through
zoneinfo — never a hardcoded UTC offset — so 06:30 ET stays 06:30 ET across
the March and November transitions. Holidays come from exchange calendars
(pandas-market-calendars), not a hand-maintained list.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

from .config import Config


@lru_cache(maxsize=8)
def _calendar(name: str):
    return mcal.get_calendar(name)


def _tz(cfg: Config) -> ZoneInfo:
    return ZoneInfo(cfg.timezone)


def local_trading_date(cfg: Config, now_utc: datetime) -> date:
    return now_utc.astimezone(_tz(cfg)).date()


def is_calendar_open(calendar_name: str, d: date) -> bool:
    cal = _calendar(calendar_name)
    sched = cal.schedule(start_date=d.isoformat(), end_date=d.isoformat())
    return not sched.empty


def is_send_day(cfg: Config, d: date) -> bool:
    """Weekday and the equity market is open (holiday-aware)."""
    if d.weekday() >= 5:
        return False
    return is_calendar_open(cfg.delivery.holiday_calendar, d)


def is_bond_market_open(cfg: Config, d: date) -> bool | None:
    """SIFMA status — surfaced in the note, not used to skip the send."""
    try:
        return is_calendar_open(cfg.delivery.bond_calendar, d)
    except Exception:  # noqa: BLE001 — informational only, never fatal
        return None


def delivery_time_utc(cfg: Config, d: date) -> datetime:
    """The UTC instant of the configured local send time on date ``d``."""
    hh, mm = (int(x) for x in cfg.delivery.send_time_local.split(":"))
    local = datetime.combine(d, time(hh, mm), tzinfo=_tz(cfg))
    return local.astimezone(ZoneInfo("UTC"))


def seconds_until_delivery(cfg: Config, now_utc: datetime) -> float:
    """Seconds from ``now_utc`` to today's local send time (negative if past)."""
    target = delivery_time_utc(cfg, local_trading_date(cfg, now_utc))
    return (target - now_utc).total_seconds()


def should_run(cfg: Config, now_utc: datetime, *, max_early_minutes: float = 75.0,
               max_late_minutes: float = 25.0) -> tuple[bool, str]:
    """Decide whether this invocation should proceed toward a send.

    Designed for two GitHub Actions crons an hour apart (so one lands
    before 6:30 ET under both EST and EDT): the invocation inside
    [send - max_early_minutes, send + max_late_minutes] proceeds (sleeping
    until the send time); anything outside exits cleanly. The 75-minute
    early bound means exactly one of the two crons qualifies in either DST
    regime, and the late bound keeps a badly delayed runner from sending a
    mid-morning note that would be worse than none.
    """
    d = local_trading_date(cfg, now_utc)
    if not is_send_day(cfg, d):
        return False, f"{d} is not a trading day"
    delta = seconds_until_delivery(cfg, now_utc)
    if delta > max_early_minutes * 60:
        return False, f"too early: {delta / 60:.0f}min before send time"
    if delta < -max_late_minutes * 60:
        return False, f"too late: {-delta / 60:.0f}min past send time — skipping rather than sending late"
    return True, f"in window: {delta / 60:.0f}min before send time"


def next_send_datetimes_utc(cfg: Config, start: date, n: int = 5) -> list[datetime]:
    """The next ``n`` send instants in UTC — useful for verification/tests."""
    out: list[datetime] = []
    d = start
    while len(out) < n:
        if is_send_day(cfg, d):
            out.append(delivery_time_utc(cfg, d))
        d += timedelta(days=1)
    return out
