"""Material-news and day-ahead gathering via the Anthropic web search tool.

Design rationale (vs the alternatives):
- RSS aggregation is free but the reliable wires (Reuters, Bloomberg) no
  longer publish usable public feeds, so RSS-only skews to second-tier
  sources and needs constant curation.
- News API free tiers (e.g. NewsAPI) delay content by 24h — useless at
  6:30am.
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


_SEARCH_SYSTEM = """You are the overnight news desk for a professional fixed-income \
morning note read by trading MDs before their 7am call. Use web search to find what \
actually happened overnight and what is scheduled today. Report ONLY facts you found \
in sources, with the source for each. Never estimate or invent a number. If you \
cannot verify an item, leave it out. Materiality bar: include an item only if a \
rates or credit trading MD would change what they do today because of it — \
prioritise anything moving rates or credit, central bank actions/commentary, large \
M&A and its financing, ratings actions, bankruptcy/distress, major earnings \
surprises, and geopolitics with a commodity or rates transmission channel. \
A quiet night is a perfectly good answer: return an empty list rather than noise."""

_SEARCH_PROMPT = """Today is {trading_date} (America/New_York). It is early morning \
before the US open. Search for:
1. Material overnight market/credit/macro news since yesterday's US close (max {max_items} items).
2. Today's US and Canadian economic data releases with scheduled times in ET and \
surveyed consensus where a source states it.
3. Fed, Bank of Canada, and ECB speakers scheduled today, with times in ET.

Then output ONLY a JSON object, no other text, in this exact shape:
{{
  "items": [{{"headline": "...", "detail": "one or two sentences, numbers only if stated by the source", "source_name": "Reuters", "url": "https://..."}}],
  "calendar": [{{"time_et": "08:30", "item": "US CPI m/m", "consensus": "0.3%" }}],
  "cb_speakers": [{{"time_et": "09:15", "item": "Fed's Waller on economic outlook"}}]
}}
Use null for a consensus you could not verify. Every news item MUST have a real \
source url. Times must be ET in 24h HH:MM."""


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
        return NewsBundle(available=False, note="ANTHROPIC_API_KEY not set — news pass skipped")
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
