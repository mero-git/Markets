"""Material-news and week-ahead gathering via the Anthropic web search tool.

Design rationale (vs the alternatives):
- RSS aggregation is free but the reliable wires (Reuters, Bloomberg) no
  longer publish usable public feeds, so RSS-only skews to second-tier
  sources and needs constant curation.
- News API free tiers (for example NewsAPI) delay content by 24h, which is
  useless for a same-day recap.
- Anthropic web search costs cents per run, returns current wire coverage,
  and every claim carries a source URL we can print. That is the tradeoff
  winner for this note.

Every item must carry a source URL; items without one are dropped. A failed
news pass degrades to an empty bundle — the run continues and the email says
the feed was unavailable rather than implying a quiet night.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Any

from ..config import Config
from ..models import MarketSnapshot

log = logging.getLogger(__name__)


@dataclass(slots=True)
class NewsItem:
    headline: str
    detail: str
    source_name: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CalendarItem:
    time_et: str | None
    item: str
    consensus: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class NewsBundle:
    items: list[NewsItem] = field(default_factory=list)
    calendar: list[CalendarItem] = field(default_factory=list)
    cb_speakers: list[CalendarItem] = field(default_factory=list)
    available: bool = True
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [i.to_dict() for i in self.items],
            "calendar": [c.to_dict() for c in self.calendar],
            "cb_speakers": [c.to_dict() for c in self.cb_speakers],
            "available": self.available,
            "note": self.note,
        }


_SEARCH_SYSTEM = """You are the news desk for a weekly markets note read by \
Canadian fixed-income professionals. Use web search to find what actually \
happened this week and what is scheduled next week. Report ONLY facts you \
found in sources, with the source for each. Never estimate or invent a \
number. If you cannot verify an item, leave it out.

Materiality bar: include an item only if a CAD rates or credit trader would \
position differently because of it. Priority order: Bank of Canada decisions \
and commentary, Canadian data that moves GoC or the CAD curve, Federal \
Reserve decisions and commentary, Canadian issuers and credit events, \
ratings actions, large M&A and the financing attached to it, and \
geopolitics with a commodity or rates transmission channel into Canada.

A quiet week is a perfectly good answer. Return an empty list rather than \
padding it with noise."""

_SEARCH_PROMPT = """The week ends {trading_date} (America/New_York). Search for:
1. The material market, credit, and macro news of this week, Canada first \
(maximum {max_items} items).
2. Canadian and US economic releases scheduled for NEXT week, with the date, \
the scheduled time in ET, and surveyed consensus where a source states it.
3. Bank of Canada and Federal Reserve speakers or decisions scheduled next \
week, with dates and times in ET.

Then output ONLY a JSON object, no other text, in this exact shape:
{{
  "items": [{{"headline": "...", "detail": "one or two sentences, numbers only if stated by the source", "source_name": "Reuters", "url": "https://..."}}],
  "calendar": [{{"time_et": "Tue 08:30", "item": "Canada CPI", "consensus": "0.3%"}}],
  "cb_speakers": [{{"time_et": "Wed 13:45", "item": "BoC rate decision"}}]
}}
Use null for a consensus you could not verify. Every news item MUST have a \
real source url. In time_et, give the weekday then the 24h time, for example \
"Tue 08:30"."""


def _extract_json(text: str) -> dict[str, Any]:
    """The model is told to output bare JSON; tolerate code fences around it."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start:end + 1])


def gather_news(cfg: Config, snapshot: MarketSnapshot) -> NewsBundle:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return NewsBundle(available=False, note="ANTHROPIC_API_KEY not set, news pass skipped")
    try:
        import anthropic

        client = anthropic.Anthropic()
        model = os.environ.get("MODEL", cfg.run.model)
        response = client.messages.create(
            model=model,
            max_tokens=6000,
            system=_SEARCH_SYSTEM,
            tools=[{
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": cfg.news.max_searches,
            }],
            messages=[{
                "role": "user",
                "content": _SEARCH_PROMPT.format(
                    trading_date=snapshot.trading_date,
                    max_items=cfg.news.max_items,
                ),
            }],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        data = _extract_json(text)

        items: list[NewsItem] = []
        for raw in (data.get("items") or [])[: cfg.news.max_items]:
            url = (raw.get("url") or "").strip()
            if not url.startswith("http"):
                log.warning("news item dropped (no source url): %s", raw.get("headline"))
                continue
            items.append(NewsItem(
                headline=str(raw.get("headline", "")).strip(),
                detail=str(raw.get("detail", "")).strip(),
                source_name=str(raw.get("source_name", "")).strip() or "source",
                url=url,
            ))

        def cal(rows: Any) -> list[CalendarItem]:
            out: list[CalendarItem] = []
            for r in rows or []:
                item = str(r.get("item", "")).strip()
                if not item:
                    continue
                out.append(CalendarItem(
                    time_et=(str(r["time_et"]).strip() if r.get("time_et") else None),
                    item=item,
                    consensus=(str(r["consensus"]).strip() if r.get("consensus") else None),
                ))
            return out

        return NewsBundle(items=items, calendar=cal(data.get("calendar")),
                          cb_speakers=cal(data.get("cb_speakers")))
    except Exception as exc:  # noqa: BLE001 — news is best-effort, never fatal
        log.error("news gathering failed: %s", exc)
        return NewsBundle(available=False, note=f"news pass failed: {exc}")
