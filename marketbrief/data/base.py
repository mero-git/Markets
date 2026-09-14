"""Shared fetch plumbing: retries with backoff, HTTP session, raw quote type.

Every source client returns :class:`RawQuote` objects; provenance and
validation are applied uniformly in the snapshot builder. One bad ticker
must never take down a run, so all retry logic raises :class:`SourceError`
at the level of a single instrument only.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, TypeVar

import requests

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) marketbrief/0.1 (+market data fetch)"
DEFAULT_TIMEOUT = 20.0

T = TypeVar("T")


class SourceError(Exception):
    """A single instrument's fetch failed after retries."""


@dataclass(slots=True)
class RawQuote:
    """Unvalidated value pair straight from a source."""

    value: float
    prev_value: float | None
    asof: datetime | None            # UTC timestamp when the source provides one
    asof_date: str | None            # ISO date for daily official series
    prev_asof_date: str | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    return s


def fetch_with_retries(
    fn: Callable[[], T],
    *,
    what: str,
    attempts: int = 3,
    base_delay: float = 1.0,
) -> T:
    """Run ``fn`` with exponential backoff and jitter; raise SourceError at the end."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — every source failure funnels here
            last = exc
            if attempt < attempts:
                delay = base_delay * (2 ** (attempt - 1)) * (1 + random.random() * 0.25)
                log.warning("fetch %s failed (attempt %d/%d): %s — retrying in %.1fs",
                            what, attempt, attempts, exc, delay)
                time.sleep(delay)
    raise SourceError(f"{what}: {last}") from last
