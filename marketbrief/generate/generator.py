"""Brief narrative generation via the Anthropic API.

The model writes only narrative: the three weekly points and a short comment
per section. Every numeric table in the email is rendered by code straight
from the snapshot, so a fabricated table is structurally impossible. The
narrative is then numerically post-validated by ``verify.verify_narrative``
and the run fails closed on any violation.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from typing import Any

from ..config import Config
from ..models import MarketSnapshot
from ..news.search import NewsBundle
from .verify import verify_narrative

log = logging.getLogger(__name__)


class GenerationError(Exception):
    """Generation failed, so the run must fail closed with no send."""


class VerificationError(GenerationError):
    """Generated text contained numbers not present in the data payload."""


@dataclass(slots=True)
class BriefNarrative:
    three_points: list[str]
    canada_comment: str | None = None
    us_comment: str | None = None
    news_intro: str | None = None
    fx_commodities_comment: str | None = None
    equities_comment: str | None = None
    watch_comment: str | None = None
    generated: bool = True
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def text_fields(self) -> dict[str, str]:
        return {
            "three_points": " ".join(self.three_points),
            "canada_comment": self.canada_comment or "",
            "us_comment": self.us_comment or "",
            "news_intro": self.news_intro or "",
            "fx_commodities_comment": self.fx_commodities_comment or "",
            "equities_comment": self.equities_comment or "",
            "watch_comment": self.watch_comment or "",
        }


_SYSTEM = """You write the Friday Markets Note, a weekly recap read by \
Canadian fixed-income professionals. The reader is a CAD rates or credit \
trader. They give this ninety seconds.

ABSOLUTE DATA RULE, the one unbreakable constraint:
Every number you write must appear verbatim in the JSON payload you are \
given, same digits. You may drop a trailing zero or a sign. Never compute, \
convert, round to new digits, estimate, or recall a number from memory. \
Never do arithmetic, because every spread and every change is already \
computed in the payload. If a number you want is not in the payload, write \
the sentence without it. Your output is machine checked against the payload \
and the run is aborted if any numeral fails to match.

SECOND RULE, accuracy over completeness: if you are not certain a statement \
is correct, leave it out. A short note that is right beats a full one that \
is not. Never assert causation the payload does not support. "Yields fell \
and equities rose" is safe. "Yields fell because equities rose" is not, \
unless a sourced news item says so.

WHAT THE NUMBERS MEAN:
The headline move is week over week, measured close to close against the \
prior Friday. The payload gives you change_week for that. One day moves are \
secondary context, available as change. Always frame commentary around the \
week unless you explicitly say otherwise. Credit spreads publish with a lag, \
so treat them as the latest available print, not Thursday's close.

VOICE:
Write like a desk strategist, not a journalist and not a chatbot. Short \
declarative sentences. Lead with what moved and by how much, then why it \
matters to a CAD book. Specific beats comprehensive.
Banned: em dashes, semicolons joining independent clauses, "investors will \
be watching closely", "remains to be seen", "mixed picture", "amid", \
"against a backdrop of", "signals", "underscores", "it is worth noting", \
rhetorical questions, and any sentence that could appear in any week's note. \
Use commas and full stops. Keep every sentence under about twenty-five words.

SECTIONS:
- three_points: exactly three, the week in three lines. Each one sentence, \
each carrying at least one number from the payload. The first must be the \
Canadian rates or credit story. Order them by what changes a CAD trader's \
positioning, not by size of move.
- canada_comment: at most three sentences on GoC curve, Canada-US spreads, \
and CORRA or the BoC target if either moved.
- us_comment: at most two sentences, strictly on what a CAD book needs from \
the US leg. Cross-market spread, direction, and credit tone.
- news_intro: one sentence framing the week's news, or null. If the news \
list is empty and the feed was available, say the week was quiet in one \
line rather than inventing content.
- fx_commodities_comment: at most two sentences. USD/CAD and the Canadian \
terms of trade come first. Bitcoin gets no comment.
- equities_comment: one sentence, TSX first. This section is context only.
- watch_comment: at most two sentences on what lands next week. Only name \
events present in the payload's news calendar or auctions. If none, null.
Any section with nothing worth saying gets null and renders as its data \
table alone.

OUTPUT: a single JSON object, nothing else, exactly this shape:
{"three_points": ["...", "...", "..."], "canada_comment": "..."|null, \
"us_comment": "..."|null, "news_intro": "..."|null, \
"fx_commodities_comment": "..."|null, "equities_comment": "..."|null, \
"watch_comment": "..."|null}"""


def build_payload(snapshot: MarketSnapshot, news: NewsBundle) -> dict[str, Any]:
    """Compact, model-facing view of the run's data."""
    sections: dict[str, list[dict[str, Any]]] = {}
    for p in snapshot.points:
        sections.setdefault(p.section, []).append({
            "label": p.label,
            "value": p.value,
            "unit": p.unit,
            "change_week": p.change_week,
            "change_week_pct": p.change_week_pct,
            "change_1d": p.change,
            "change_1d_pct": p.change_pct,
            "change_unit": p.change_unit,
            "asof": p.asof.isoformat() if p.asof else p.asof_date,
            "week_ago_date": p.week_asof_date,
            "note": p.note,
        })
    return {
        "week_ending": snapshot.trading_date,
        "bond_market_open_today": snapshot.sifma_open_today,
        "markets": sections,
        "auctions_today": [a.to_dict() for a in snapshot.auctions_today],
        "auction_results_this_week": [a.to_dict() for a in snapshot.auction_results],
        "news": news.to_dict(),
        "data_gaps": sorted({f.key for f in snapshot.failures}
                            | {r.key for r in snapshot.rejections}),
    }


def _extract_json(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise GenerationError("model output contained no JSON object")
    return json.loads(text[start:end + 1])


def placeholder_narrative() -> BriefNarrative:
    return BriefNarrative(
        three_points=["[SAMPLE] Narrative generation skipped because "
                      "ANTHROPIC_API_KEY is not set. Tables below are real "
                      "fetched data."],
        generated=False,
    )


def _clean(value: Any) -> str | None:
    """Strip the characters the house style bans, defensively."""
    if not value:
        return None
    text = str(value).replace("—", ", ").replace(" -- ", ", ")
    return " ".join(text.split()) or None


def generate_narrative(cfg: Config, snapshot: MarketSnapshot,
                       news: NewsBundle) -> BriefNarrative:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise GenerationError("ANTHROPIC_API_KEY not set")

    import anthropic

    client = anthropic.Anthropic()
    model = os.environ.get("MODEL", cfg.run.model)
    payload = build_payload(snapshot, news)
    user_msg = ("Write this week's note from this payload only:\n\n"
                + json.dumps(payload, indent=1, default=str))

    last_error: Exception | None = None
    for attempt in range(2):  # one retry, fed the specific violation
        try:
            response = client.messages.create(
                model=model,
                max_tokens=cfg.generation.max_tokens,
                output_config={"effort": cfg.generation.effort},
                system=_SYSTEM,
                messages=[{"role": "user", "content": user_msg}],
            )
            if response.stop_reason == "refusal":
                raise GenerationError("model refused the generation request")
            text = "".join(b.text for b in response.content if b.type == "text")
            data = _extract_json(text)

            points = [_clean(x) for x in (data.get("three_points") or [])]
            narrative = BriefNarrative(
                three_points=[p for p in points if p][:3],
                canada_comment=_clean(data.get("canada_comment")),
                us_comment=_clean(data.get("us_comment")),
                news_intro=_clean(data.get("news_intro")),
                fx_commodities_comment=_clean(data.get("fx_commodities_comment")),
                equities_comment=_clean(data.get("equities_comment")),
                watch_comment=_clean(data.get("watch_comment")),
                model=model,
            )
            if len(narrative.three_points) != 3:
                raise GenerationError(
                    f"expected three weekly points, got {len(narrative.three_points)}")

            failures = verify_narrative(narrative.text_fields(), snapshot, news)
            if failures:
                raise VerificationError(
                    f"unverified numbers in generated text: {failures}")
            return narrative
        except (VerificationError, GenerationError, json.JSONDecodeError) as exc:
            last_error = exc
            log.warning("generation attempt %d failed: %s", attempt + 1, exc)
            if attempt == 0:
                user_msg += ("\n\nYour previous attempt was rejected: "
                             f"{exc}. Follow the output shape and the absolute "
                             "data rule exactly.")
    raise GenerationError(f"generation failed after retry: {last_error}")
