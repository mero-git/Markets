"""Recipient staging (fail closed) and the rendered note."""

from __future__ import annotations

import pytest

from marketbrief.deliver.email_html import render_email
from marketbrief.deliver.recipients import RecipientError, resolve_recipients
from marketbrief.generate.generator import BriefNarrative
from marketbrief.news.search import CalendarItem, NewsBundle

from test_verify import _snapshot, NEWS


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("OWNER_EMAIL", "RECIPIENTS_TEST", "RECIPIENTS_FULL",
                "EMAIL_FROM", "EMAIL_REPLY_TO"):
        monkeypatch.delenv(var, raising=False)


class TestRecipients:
    def test_self_tier_owner_only(self, monkeypatch):
        monkeypatch.setenv("OWNER_EMAIL", "me@example.com")
        monkeypatch.setenv("EMAIL_FROM", "Note <note@resend.dev>")
        r = resolve_recipients("self")
        assert r.to == ["me@example.com"] and r.bcc == []

    def test_full_tier_uses_bcc_not_to(self, monkeypatch):
        monkeypatch.setenv("OWNER_EMAIL", "me@example.com")
        monkeypatch.setenv("EMAIL_FROM", "Note <note@example.ca>")
        monkeypatch.setenv("RECIPIENTS_FULL", "md1@bank.com, md2@bank.com")
        r = resolve_recipients("full")
        assert r.to == ["me@example.com"]
        assert r.bcc == ["md1@bank.com", "md2@bank.com"]

    def test_shared_test_domain_blocks_beyond_self(self, monkeypatch):
        monkeypatch.setenv("OWNER_EMAIL", "me@example.com")
        monkeypatch.setenv("EMAIL_FROM", "Note <onboarding@resend.dev>")
        monkeypatch.setenv("RECIPIENTS_TEST", "friend@example.com")
        with pytest.raises(RecipientError, match="shared test domain"):
            resolve_recipients("test")

    def test_empty_list_fails_closed(self, monkeypatch):
        monkeypatch.setenv("OWNER_EMAIL", "me@example.com")
        monkeypatch.setenv("EMAIL_FROM", "Note <n@example.ca>")
        with pytest.raises(RecipientError, match="RECIPIENTS_FULL"):
            resolve_recipients("full")

    def test_bad_address_rejected(self, monkeypatch):
        monkeypatch.setenv("OWNER_EMAIL", "not-an-email")
        monkeypatch.setenv("EMAIL_FROM", "Note <n@example.ca>")
        with pytest.raises(RecipientError, match="invalid email"):
            resolve_recipients("self")

    def test_unknown_tier_rejected(self):
        with pytest.raises(RecipientError, match="unknown"):
            resolve_recipients("everyone")


def _narrative() -> BriefNarrative:
    return BriefNarrative(
        three_points=[
            "GoC 10Y fell 21bp on the week to 3.78%.",
            "HY OAS widened 24bp to 340bp.",
            "Canada-US 10Y at -118bp, 9bp wider.",
        ],
        canada_comment="Front end led the rally.",
        us_comment="Treasuries outran GoC by 9bp at the 10Y point.",
        model="test-model",
    )


class TestRender:
    def test_weekly_framing_and_sections(self, cfg):
        subject, html, text = render_email(cfg, _snapshot(), _narrative(), NEWS,
                                           tier="self")
        assert "Friday Markets Note" in subject and "week ending" in subject
        assert "Blackleaf" not in html and "Blackleaf" not in text
        assert "The week in three" in html
        assert "Canada: rates" in html
        assert "1 week" in html  # weekly column is present and labelled
        assert "3.78%" in html

    def test_canada_precedes_united_states(self, cfg):
        _, html, _ = render_email(cfg, _snapshot(), _narrative(), NEWS, tier="self")
        assert html.index("Canada: rates") < html.index("United States")

    def test_section_order_matches_house_style(self, cfg):
        news = NewsBundle(items=NEWS.items, calendar=NEWS.calendar)
        n = _narrative()
        # Give every section something to say so all of them render.
        n.fx_commodities_comment = "USD/CAD held its range."
        n.equities_comment = "TSX outperformed."
        n.watch_comment = "Canada CPI lands Tuesday."
        _, html, _ = render_email(cfg, _snapshot(), n, news, tier="self")
        order = ["Canada: rates", "United States", "News", "FX and commodities",
                 "Equities", "What to watch"]
        found = [html.index(s) for s in order]
        assert found == sorted(found)

    def test_no_em_dashes_in_chrome(self, cfg):
        _, html, text = render_email(cfg, _snapshot(), _narrative(), NEWS, tier="self")
        assert "—" not in html and "—" not in text

    def test_outlook_safe_layout(self, cfg):
        _, html, _ = render_email(cfg, _snapshot(), _narrative(), NEWS, tier="self")
        assert "<table" in html
        for banned in ("display:flex", "display:grid", "@media"):
            assert banned not in html

    def test_bcc_list_never_appears_in_body(self, cfg):
        _, html, text = render_email(cfg, _snapshot(), _narrative(), NEWS, tier="full")
        assert "bank.com" not in html and "bank.com" not in text

    def test_unsubscribe_and_attribution_present(self, cfg):
        _, html, text = render_email(cfg, _snapshot(), _narrative(), NEWS, tier="self")
        assert "unsubscribe" in html and "unsubscribe" in text
        assert cfg.email.sender_name in html

    def test_empty_news_feed_states_it_rather_than_padding(self, cfg):
        quiet = NewsBundle(items=[], available=False)
        n = _narrative()
        n.news_intro = None
        _, html, _ = render_email(cfg, _snapshot(), n, quiet, tier="self")
        assert "News feed unavailable this run." in html

    def test_degraded_banner_and_subject(self, cfg):
        snap = _snapshot()
        snap.health.degraded = True
        snap.health.reasons = ["critical fields missing: goc_10y"]
        subject, html, _ = render_email(cfg, snap, _narrative(), NEWS, tier="self")
        assert subject.startswith("[DEGRADED]") and "Degraded run" in html

    def test_what_to_watch_lists_calendar(self, cfg):
        news = NewsBundle(items=[], calendar=[
            CalendarItem(time_et="08:30", item="Canada GDP", consensus="0.2%")])
        _, html, text = render_email(cfg, _snapshot(), _narrative(), news, tier="self")
        assert "What to watch" in html
        assert "08:30 ET, Canada GDP" in text and "consensus 0.2%" in text
