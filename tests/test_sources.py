"""Source parsers against fixture payloads — all network mocked."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from marketbrief.data import boc, fred, yahoo
from marketbrief.data.base import SourceError, fetch_with_retries

from conftest import FakeResponse, FakeSession

NOW_TS = int(datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc).timestamp())


def yahoo_payload(price=6512.25, prev=6480.0, ts=NOW_TS, *, include_prev=True):
    meta = {"regularMarketPrice": price, "regularMarketTime": ts,
            "exchangeTimezoneName": "America/New_York"}
    if include_prev:
        meta["previousClose"] = prev
    return {"chart": {"result": [{
        "meta": meta,
        "timestamp": [NOW_TS - 3 * 86400, NOW_TS - 86400, NOW_TS],
        "indicators": {"quote": [{"close": [6410.0, prev, price]}]},
    }], "error": None}}


class TestYahoo:
    def test_parses_price_prev_and_timestamp(self):
        session = FakeSession([FakeResponse(json_data=yahoo_payload())])
        quote = yahoo.fetch_quote(session, "ES=F")
        assert quote.value == 6512.25
        assert quote.prev_value == 6480.0
        assert quote.asof == datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)

    def test_prev_close_from_bars_when_meta_missing(self):
        session = FakeSession([FakeResponse(json_data=yahoo_payload(include_prev=False))])
        quote = yahoo.fetch_quote(session, "ES=F")
        assert quote.prev_value == 6480.0  # last bar before the quote's date

    def test_error_payload_raises(self):
        payload = {"chart": {"result": None, "error": {"code": "Not Found"}}}
        session = FakeSession([FakeResponse(json_data=payload)])
        with pytest.raises(ValueError):
            yahoo.fetch_quote(session, "BOGUS")

    def test_missing_price_raises_not_fabricates(self):
        payload = {"chart": {"result": [{"meta": {}, "timestamp": [],
                                         "indicators": {"quote": [{}]}}], "error": None}}
        session = FakeSession([FakeResponse(json_data=payload)])
        with pytest.raises(ValueError):
            yahoo.fetch_quote(session, "ES=F")


FRED_CSV = """observation_date,DGS10
2026-09-08,4.05
2026-09-09,4.08
2026-09-10,.
2026-09-11,4.12
"""


class TestFred:
    def test_csv_skips_missing_and_returns_last_two(self, monkeypatch):
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        session = FakeSession([FakeResponse(text=FRED_CSV)])
        quote = fred.fetch_series(session, "DGS10")
        assert quote.value == 4.12 and quote.asof_date == "2026-09-11"
        assert quote.prev_value == 4.08 and quote.prev_asof_date == "2026-09-09"

    def test_api_used_when_key_present(self, monkeypatch):
        monkeypatch.setenv("FRED_API_KEY", "test-key")
        payload = {"observations": [
            {"date": "2026-09-10", "value": "4.10"},
            {"date": "2026-09-11", "value": "."},
            {"date": "2026-09-12", "value": "4.15"},
        ]}
        session = FakeSession([FakeResponse(json_data=payload)])
        quote = fred.fetch_series(session, "DGS10")
        assert quote.value == 4.15 and quote.prev_value == 4.10
        assert "api.stlouisfed.org" in session.calls[0][0]

    def test_empty_series_raises(self, monkeypatch):
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        session = FakeSession([FakeResponse(text="observation_date,DGS10\n2026-09-11,.\n")])
        with pytest.raises(ValueError):
            fred.fetch_series(session, "DGS10")


class TestBoc:
    def test_parses_valet_observations(self):
        payload = {"observations": [
            {"d": "2026-09-10", "BD.CDN.10YR.DQ.YLD": {"v": "3.18"}},
            {"d": "2026-09-11", "BD.CDN.10YR.DQ.YLD": {"v": "3.21"}},
        ]}
        session = FakeSession([FakeResponse(json_data=payload)])
        quote = boc.fetch_series(session, "BD.CDN.10YR.DQ.YLD")
        assert quote.value == 3.21 and quote.prev_value == 3.18
        assert quote.asof_date == "2026-09-11"


class TestRetries:
    def test_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda _s: None)
        attempts = {"n": 0}

        def flaky():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError("transient")
            return 42

        assert fetch_with_retries(flaky, what="flaky", attempts=3) == 42

    def test_exhausted_retries_raise_source_error(self, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda _s: None)

        def dead():
            raise RuntimeError("down")

        with pytest.raises(SourceError):
            fetch_with_retries(dead, what="dead", attempts=2)
