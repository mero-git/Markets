"""Recipient staging (fail-closed) and Outlook-safe email rendering."""

from __future__ import annotations

import pytest

from marketbrief.deliver.email_html import render_email
from marketbrief.deliver.recipients import RecipientError, resolve_recipients
from marketbrief.generate.generator import BriefNarrative
from marketbrief.news.search import CalendarItem, NewsBundle, NewsItem

from test_verify import _snapshot, NEWS


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("OWNER_EMAIL", "RECIPIENTS_TEST", "RECIPIENTS_FULL",
                "EMAIL_FROM", "EMAIL_REPLY_TO"):
        monkeypatch.delenv(var, raising=False)


class TestRecipients:
    def test_self_tier_owner_only(self, monkeypatch):
        monkeypatch.setenv("OWNER_EMAIL", "me@example.com")
        monkeypatch.setenv("EMAIL_FROM", "Brief <brief@resend.dev>")
        r = resolve_recipients("self")
        assert r.to == ["me@example.com"] and r.bcc == []

    def test_full_tier_uses_bcc_not_to(self, monkeypatch):
        monkeypatch.setenv("OWNER_EMAIL", "me@example.com")
        monkeypatch.setenv("EMAIL_FROM", "Brief <brief@blackleaf.example>")
        monkeypatch.setenv("RECIPIENTS_FULL", "md1@bank.com, md2@bank.com")
        r = resolve_recipients("full")
        assert r.to == ["me@example.com"]
        assert r.bcc == ["md1@bank.com", "md2@bank.com"]

    def test_shared_test_domain_blocks_beyond_self(self, monkeypatch):
        monkeypatch.setenv("OWNER_EMAIL", "me@example.com")
        monkeypatch.setenv("EMAIL_FROM", "Brief <onboarding@resend.dev>")
        monkeypatch.setenv("RECIPIENTS_TEST", "friend@example.com")
        with pytest.raises(RecipientError, match="shared test domain"):
            resolve_recipients("test")

    def test_empty_list_fails_closed(self, monkeypatch):
        monkeypatch.setenv("OWNER_EMAIL", "me@example.com")
        monkeypatch.setenv("EMAIL_FROM", "Brief <b@blackleaf.example>")
        with pytest.raises(RecipientError, match="RECIPIENTS_FULL"):
            resolve_recipients("full")

    def test_bad_address_rejected(self, monkeypatch):
        monkeypatch.setenv("OWNER_EMAIL", "not-an-email")
        monkeypatch.setenv("EMAIL_FROM", "Brief <b@blackleaf.example>")
        with pytest.raises(RecipientError, match="invalid email"):
            resolve_recipients("self")

    def test_unknown_tier_rejected(self):
        with pytest.raises(RecipientError, match="unknown"):
            resolve_recipients("everyone")


def _narrative() -> BriefNarrative:
    return BriefNarrative(
        tldr=["10Y unchanged at 4.96 into supply; HY 5bp tighter at 265."],
        curve_comment="2s10s 6bp flatter at 33bp.",
        credit_comment=None,
        model="test-model",
    )


class TestEmailRender:
    def test_renders_all_parts(self, cfg):
        subject, html, text = render_email(cfg, _snapshot(), _narrative(), NEWS,
                                           tier="self")
        assert "Blackleaf Capital News Update" in subject
        assert "4.96" in html and "4.96" in text
        assert "Acme downgraded" in html
        assert "https://reuters.com" in html
        assert "unsubscribe" in html and "unsubscribe" in text
        # Outlook constraints: table layout, no flexbox/grid.
        assert "<table" in html
        assert "flex" not in html and "grid" not in html

    def test_bcc_invisibility_is_structural(self, cfg):
        # Recipients never appear in the rendered body at all.
        _, html, text = render_email(cfg, _snapshot(), _narrative(), NEWS, tier="full")
        assert "bank.com" not in html and "bank.com" not in text

    def test_empty_news_section_omitted(self, cfg):
        empty_news = NewsBundle(items=[], calendar=[], cb_speakers=[])
        n = _narrative()
        n.news_intro = None
        _, html, _ = render_email(cfg, _snapshot(), n, empty_news, tier="self")
        assert "Material News" not in html

    def test_quiet_overnight_one_liner(self, cfg):
        news = NewsBundle(items=[])
        n = _narrative()
        n.news_intro = "Quiet overnight; nothing material for rates or credit."
        _, html, _ = render_email(cfg, _snapshot(), n, news, tier="self")
        assert "Quiet overnight" in html

    def test_degraded_banner_on_self_copy(self, cfg):
        snap = _snapshot()
        snap.health.degraded = True
        snap.health.reasons = ["critical fields missing: ust_10y"]
        subject, html, _ = render_email(cfg, snap, _narrative(), NEWS, tier="self")
        assert subject.startswith("[DEGRADED]")
        assert "DEGRADED RUN" in html

    def test_on_deck_includes_calendar_and_speakers(self, cfg):
        news = NewsBundle(
            items=[], calendar=[CalendarItem(time_et="08:30", item="US CPI m/m",
                                             consensus="0.3%")],
            cb_speakers=[CalendarItem(time_et="09:15", item="Fed's Chair testifies")])
        _, html, text = render_email(cfg, _snapshot(), _narrative(), news, tier="self")
        assert "On Deck Today" in html
        assert "08:30 ET" in text and "cons. 0.3%" in text
